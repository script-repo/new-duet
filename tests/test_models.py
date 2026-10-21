"""Model layer: each provider's wire format, retries, JSON parsing, and per-role routing."""

from __future__ import annotations

import json

import httpx
import pytest

from tandem.config import AppConfig, ModelProfile
from tandem.models import ModelError, ModelRouter, build_client
from tandem.models.base import Message, ToolCall, ToolSpec
from tandem.models.mock import MockModel
from tandem.models.providers import gemini_schema
from tandem.util import extract_json

TOOLS = [
    ToolSpec(
        "email_send",
        "Send",
        {
            "type": "object",
            "properties": {"to": {"type": "string"}},
            "required": ["to"],
            "additionalProperties": False,
        },
    )
]
CONVO = [
    Message("system", "be brief"),
    Message("user", "email sam"),
    Message("assistant", "", tool_calls=[ToolCall("c1", "email_send", {"to": "sam@x.example"})]),
    Message("tool", "sent", tool_call_id="c1", name="email_send"),
]


def transport(handler):
    calls = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request, len(calls))

    return httpx.MockTransport(wrapped), calls


async def test_anthropic_wire_format_and_tool_use(monkeypatch):
    monkeypatch.setenv("ANT_KEY", "sk-ant-test-1234567890abcdef")

    def handler(req, n):
        body = json.loads(req.content)
        assert req.url.path == "/v1/messages"
        assert req.headers["x-api-key"] == "sk-ant-test-1234567890abcdef"
        assert body["system"] == "be brief"
        assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
        assert body["messages"][1]["content"][0]["type"] == "tool_use"
        assert body["messages"][2]["content"][0] == {
            "type": "tool_result",
            "tool_use_id": "c1",
            "content": "sent",
        }
        assert body["tools"][0]["input_schema"]["required"] == ["to"]
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "ok "},
                    {"type": "tool_use", "id": "t2", "name": "email_send", "input": {"to": "a"}},
                ],
                "usage": {"input_tokens": 10, "output_tokens": 3},
                "stop_reason": "tool_use",
            },
        )

    t, _calls = transport(handler)
    client = build_client(
        ModelProfile(name="s", provider="anthropic", model="claude-x", api_key_env="ANT_KEY"), transport=t
    )
    resp = await client.complete(CONVO, TOOLS)
    assert (
        resp.text == "ok "
        and resp.tool_calls[0].name == "email_send"
        and resp.tool_calls[0].args == {"to": "a"}
    )
    assert resp.usage.input_tokens == 10 and client.usage.output_tokens == 3


async def test_openai_compatible_wire_format(monkeypatch):
    def handler(req, n):
        body = json.loads(req.content)
        assert str(req.url) == "http://nai.local/api/v1/chat/completions"
        assert req.headers["authorization"] == "Bearer not-needed"
        assert body["messages"][0] == {"role": "system", "content": "be brief"}
        assert body["messages"][2]["tool_calls"][0]["function"]["name"] == "email_send"
        assert body["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": "sent"}
        assert "max_tokens" in body and "max_completion_tokens" not in body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {"id": "x1", "function": {"name": "email_send", "arguments": '{"to": "b"}'}}
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2},
            },
        )

    t, _ = transport(handler)
    client = build_client(
        ModelProfile(
            name="c", provider="openai_compatible", model="llama", base_url="http://nai.local/api/v1"
        ),
        transport=t,
    )
    resp = await client.complete(CONVO, TOOLS)
    assert resp.tool_calls[0].args == {"to": "b"} and resp.usage.input_tokens == 7


async def test_openai_json_mode_and_token_param(monkeypatch):
    monkeypatch.setenv("OAI", "sk-proj-abcdefghijklmnop1234")

    def handler(req, n):
        body = json.loads(req.content)
        assert body["response_format"] == {"type": "json_object"}
        assert "max_completion_tokens" in body
        return httpx.Response(200, json={"choices": [{"message": {"content": '```json\n{"a": 1}\n```'}}]})

    t, _ = transport(handler)
    client = build_client(
        ModelProfile(name="o", provider="openai", model="gpt", api_key_env="OAI"), transport=t
    )
    assert await client.complete_json([Message("user", "hi")]) == {"a": 1}


async def test_gemini_wire_format(monkeypatch):
    monkeypatch.setenv("GKEY", "AIza-test-not-a-key")

    def handler(req, n):
        body = json.loads(req.content)
        assert req.url.path.endswith("/models/gemini-x:generateContent")
        assert req.headers["x-goog-api-key"].startswith("AIza")
        assert body["systemInstruction"]["parts"][0]["text"] == "be brief"
        assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
        assert body["contents"][2]["parts"][0]["functionResponse"]["name"] == "email_send"
        decl = body["tools"][0]["functionDeclarations"][0]
        assert "additionalProperties" not in decl["parameters"]
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"functionCall": {"name": "email_send", "args": {"to": "c"}}}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 5},
            },
        )

    t, _ = transport(handler)
    client = build_client(
        ModelProfile(name="g", provider="gemini", model="gemini-x", api_key_env="GKEY"), transport=t
    )
    resp = await client.complete(CONVO, TOOLS)
    assert resp.tool_calls[0].name == "email_send" and resp.tool_calls[0].args == {"to": "c"}


async def test_retry_on_429_then_success(monkeypatch):
    monkeypatch.setenv("ANT_KEY", "k" * 20)
    monkeypatch.setattr("tandem.models.base.asyncio.sleep", _no_sleep)

    def handler(req, n):
        if n < 3:
            return httpx.Response(429, headers={"retry-after": "0"}, text="slow down")
        return httpx.Response(200, json={"content": [{"type": "text", "text": "fine"}]})

    t, calls = transport(handler)
    client = build_client(
        ModelProfile(name="s", provider="anthropic", model="m", api_key_env="ANT_KEY"), transport=t
    )
    assert (await client.complete([Message("user", "hi")])).text == "fine"
    assert len(calls) == 3


async def test_non_retryable_error_raises(monkeypatch):
    monkeypatch.setenv("ANT_KEY", "k" * 20)
    t, calls = transport(lambda req, n: httpx.Response(400, text="bad request"))
    client = build_client(
        ModelProfile(name="s", provider="anthropic", model="m", api_key_env="ANT_KEY"), transport=t
    )
    with pytest.raises(ModelError, match="400"):
        await client.complete([Message("user", "hi")])
    assert len(calls) == 1


async def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    client = build_client(ModelProfile(name="s", provider="anthropic", model="m", api_key_env="NOPE"))
    with pytest.raises(ModelError, match="NOPE"):
        await client.complete([Message("user", "hi")])


async def test_openai_compatible_requires_base_url():
    client = build_client(ModelProfile(name="c", provider="openai_compatible", model="m"))
    with pytest.raises(ModelError, match="base_url"):
        await client.complete([Message("user", "hi")])


async def test_complete_json_repairs_once():
    class Flaky(MockModel):
        n = 0

        async def _complete(self, messages, tools, **kw):
            from tandem.models.base import ModelResponse

            self.n += 1
            return ModelResponse("not json" if self.n == 1 else '{"ok": true}')

    m = Flaky(ModelProfile(provider="mock"), "chat")
    assert await m.complete_json([Message("user", "x")]) == {"ok": True}
    assert m.n == 2


def test_router_picks_cheap_for_triage():
    cfg = AppConfig(
        models={
            "cheap": ModelProfile(provider="mock", model="c"),
            "strong": ModelProfile(provider="mock", model="s"),
        }
    )
    r = ModelRouter(cfg)
    assert r.profile("triage").model == "c"
    assert r.profile("worker").model == "s"
    cfg.roles = {"worker": "cheap"}
    assert ModelRouter(cfg).profile("worker").model == "c"
    assert set(r.public()) == {"triage", "brief", "chat", "worker"}


def test_ollama_default_base_url():
    client = build_client(ModelProfile(provider="ollama", model="llama3"))
    assert client.base_url() == "http://localhost:11434/v1"  # type: ignore[attr-defined]


def test_unknown_provider():
    p = ModelProfile(provider="mock")
    object.__setattr__(p, "provider", "nope")
    with pytest.raises(ValueError):
        build_client(p)


def test_gemini_schema_strips_unsupported_keys():
    s = gemini_schema(
        {
            "type": "object",
            "additionalProperties": False,
            "$schema": "x",
            "properties": {"a": {"type": "string", "default": "q"}},
        }
    )
    assert s == {"type": "object", "properties": {"a": {"type": "string"}}}


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure! ```json\n{"a": 2}\n``` done') == {"a": 2}
    assert extract_json('prefix {"a": 3} suffix') == {"a": 3}
    with pytest.raises(ValueError):
        extract_json("no json here")


async def test_mock_triage_is_deterministic():
    m = MockModel(ModelProfile(provider="mock"), "triage")
    data = {
        "kind": "triage",
        "events": [
            {
                "id": "1",
                "kind": "email",
                "title": "Payment failed",
                "body": "card declined, service suspended today",
            },
            {"id": "2", "kind": "email", "title": "Hello", "body": "nice to meet you"},
            {"id": "3", "kind": "email", "title": "Deals", "body": "", "rule_flags": ["bulk"]},
        ],
    }
    out = await m.complete_json([Message("user", f"<data>{json.dumps(data)}</data>")])
    by = {r["id"]: r for r in out["results"]}
    assert by["1"]["urgency"] == "critical" and by["1"]["action"] == "notify"
    assert by["2"]["action"] == "brief"
    assert by["3"]["action"] == "ignore"


async def _no_sleep(*_a, **_k):
    return None
