"""OpenTelemetry GenAI traces (SPEC §15.6) and the harness manifest that quality tools grade."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import yaml

from tandem import quality, telemetry
from tandem.config import TelemetryConfig
from tandem.runtime import Runtime
from tandem.security import Redactor
from tests.conftest import mock_cfg

ROOT = Path(__file__).resolve().parents[1]


class Collector:
    """A fake OTLP/HTTP endpoint that keeps every export request."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []

    def transport(self) -> httpx.MockTransport:
        def handle(req: httpx.Request) -> httpx.Response:
            self.requests.append(json.loads(req.content))
            self.headers.append(req.headers)
            return httpx.Response(self.status, json={})

        return httpx.MockTransport(handle)

    def spans(self) -> list[dict[str, Any]]:
        return [
            s
            for r in self.requests
            for rs in r["resourceSpans"]
            for ss in rs["scopeSpans"]
            for s in ss["spans"]
        ]


def attrs(span: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for a in span["attributes"]:
        v = a["value"]
        out[a["key"]] = next(iter(v.values())) if "arrayValue" not in v else v["arrayValue"]["values"]
    return out


def tracer(collector: Collector, **kw: Any) -> telemetry.Tracer:
    return telemetry.Tracer("http://collector.test/v1/traces", transport=collector.transport(), **kw)


async def test_spans_nest_and_export_as_otlp_json() -> None:
    col = Collector()
    t = tracer(col, resource={"ambientq.team": "ops"})
    with t.agent_span("chat", "chat-1") as root:
        with t.span("chat mock-strong", {"gen_ai.operation.name": "chat"}, kind=telemetry.KIND_CLIENT):
            pass
        with t.span("execute_tool email_read", {"gen_ai.tool.name": "email_read"}):
            pass
    with t.agent_span("triage", "tri-1"):
        pass
    assert await t.flush() == 4
    spans = col.spans()
    by_name = {s["name"]: s for s in spans}
    model, tool = by_name["chat mock-strong"], by_name["execute_tool email_read"]
    roots = [s for s in spans if s["name"] == "invoke_agent tandem"]
    assert len(roots) == 2 and len({s["traceId"] for s in roots}) == 2, "each agent run is its own trace"
    assert model["parentSpanId"] == root.span_id and tool["parentSpanId"] == root.span_id
    assert model["traceId"] == root.trace_id and model["kind"] == telemetry.KIND_CLIENT
    assert attrs(model)["gen_ai.conversation.id"] == "chat-1", "children inherit the conversation id"
    assert attrs(roots[0])["gen_ai.agent.name"] == "tandem"
    assert len(root.trace_id) == 32 and len(root.span_id) == 16
    assert int(model["endTimeUnixNano"]) >= int(model["startTimeUnixNano"])
    resource = attrs(col.requests[0]["resourceSpans"][0]["resource"])
    assert resource["service.name"] == "tandem" and resource["ambientq.team"] == "ops"
    assert col.headers[0]["content-type"] == "application/json"
    assert t.exported == 4 and not t.queue


async def test_errors_mark_the_span_and_still_raise() -> None:
    col = Collector()
    t = tracer(col)
    try:
        with t.agent_span("task", "t-1"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    await t.flush()
    (span,) = col.spans()
    assert span["status"] == {"code": telemetry.STATUS_ERROR, "message": "boom"}
    assert attrs(span)["error.type"] == "RuntimeError"


async def test_disabled_is_a_no_op() -> None:
    t = telemetry.Tracer(None)
    assert not t.enabled
    with t.agent_span("chat", "c") as sp:
        assert sp is None
    assert not t.queue and await t.flush() == 0
    assert telemetry.from_config(TelemetryConfig(), str, {}).enabled is False
    on_without_endpoint = telemetry.from_config(TelemetryConfig(enabled=True), str, {})
    assert on_without_endpoint.enabled is False, "enabled needs somewhere to send"


def test_config_reads_standard_otel_env() -> None:
    env = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://aq:8080/", "OTEL_EXPORTER_OTLP_HEADERS": "x-token=abc, a=b"}
    t = telemetry.from_config(TelemetryConfig(enabled=True, team="growth"), str, env)
    assert t.enabled and t.endpoint == "http://aq:8080/v1/traces"
    assert t.headers == {"x-token": "abc", "a": "b"} and t.resource == {"ambientq.team": "growth"}
    explicit = {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://c/v1/traces", "AQ": "k=v"}
    t2 = telemetry.from_config(TelemetryConfig(enabled=True, headers_env="AQ"), str, explicit)
    assert t2.endpoint == "http://c/v1/traces" and t2.headers == {"k": "v"}
    # TANDEM_TELEMETRY switches it on (docker compose) or off without editing tandem.yaml.
    assert telemetry.from_config(TelemetryConfig(), str, {**env, "TANDEM_TELEMETRY": "1"}).enabled
    assert not telemetry.from_config(
        TelemetryConfig(enabled=True), str, {**env, "TANDEM_TELEMETRY": "0"}
    ).enabled


async def test_export_failure_never_breaks_the_agent() -> None:
    col = Collector(status=503)
    t = tracer(col)
    with t.agent_span("brief", "b"):
        pass
    assert await t.flush() == 0
    assert t.dropped == 1 and not t.queue

    def refuse(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    t2 = telemetry.Tracer("http://nowhere.test/v1/traces", transport=httpx.MockTransport(refuse))
    with t2.agent_span("brief", "b"):
        pass
    await t2.stop()
    assert t2.dropped == 1


def test_queue_is_bounded() -> None:
    t = telemetry.Tracer("http://c.test", max_queue=3)
    for i in range(5):
        with t.agent_span("triage", f"t{i}"):
            pass
    assert len(t.queue) == 3 and t.dropped == 2


async def test_content_is_redacted_and_optional() -> None:
    col = Collector()
    t = tracer(col, redact=Redactor().text)
    secret = "sk-ant-" + "a1B2" * 8
    from tandem.models.base import Message, ModelResponse

    with t.span("chat m") as sp:
        t.record_model_call(
            sp,
            [Message("system", "Be brief."), Message("user", f"my key is {secret}")],
            ModelResponse(text=f"Noted {secret}", model="m"),
            None,
        )
    await t.flush()
    a = attrs(col.spans()[0])
    assert secret not in json.dumps(a)
    assert json.loads(a["gen_ai.system_instructions"]) == [{"type": "text", "content": "Be brief."}]
    user = json.loads(a["gen_ai.input.messages"])[0]
    assert user["role"] == "user" and user["parts"][0]["type"] == "text"
    assert json.loads(a["gen_ai.output.messages"])[0]["parts"][0]["content"].startswith("Noted ")

    quiet = tracer(Collector(), capture_content=False)
    with quiet.span("chat m") as sp:
        quiet.record_model_call(sp, [Message("user", "hi")], ModelResponse(text="yo", model="m"), None)
    assert "gen_ai.input.messages" not in quiet.queue[0].attributes
    assert quiet.queue[0].attributes["gen_ai.response.model"] == "m"


async def test_a_real_day_emits_triage_chat_model_and_tool_spans(make_rt: Callable[..., Runtime]) -> None:
    cfg = mock_cfg()
    cfg.telemetry = TelemetryConfig(enabled=True, endpoint="http://collector.test/v1/traces")
    rt = make_rt(cfg=cfg)
    col = Collector()
    rt.tracer.transport = col.transport()
    await rt.start()
    try:
        assert telemetry.tracer() is rt.tracer
        await rt.checkin()
        text = "What's on my calendar today?"
        rt.store.add_message("user", text)
        await rt.chat.reply(text)
    finally:
        await rt.stop()
    assert telemetry.tracer() is telemetry.NOOP, "stop() restores the no-op tracer"
    spans = col.spans()
    kinds = {attrs(s).get("tandem.run.kind") for s in spans if s["name"] == "invoke_agent tandem"}
    assert {"triage", "chat"} <= kinds
    models = [s for s in spans if s["name"].startswith("chat ")]
    assert models and all(attrs(s)["gen_ai.operation.name"] == "chat" for s in models)
    m = attrs(models[0])
    assert m["gen_ai.provider.name"] == "tandem.mock" and m["gen_ai.request.model"].startswith("mock-")
    assert "gen_ai.input.messages" in m and "gen_ai.usage.output_tokens" in m
    tools = [attrs(s) for s in spans if s["name"].startswith("execute_tool ")]
    assert tools, "the chat reply looked something up"
    assert all(t["gen_ai.operation.name"] == "execute_tool" and "gen_ai.tool.call.id" in t for t in tools)
    ids = {s["spanId"] for s in spans}
    assert all(s.get("parentSpanId") in ids for s in spans if "parentSpanId" in s), "no orphan spans"


def test_default_runtime_has_telemetry_off(make_rt: Callable[..., Runtime]) -> None:
    rt = make_rt()
    assert rt.tracer.enabled is False and telemetry.tracer() is telemetry.NOOP


def test_harness_manifest_is_current_and_complete() -> None:
    assert quality.write(ROOT, check=True) == [], "run `uv run tandem harness export`"
    man = yaml.safe_load((ROOT / "harness.yaml").read_text())
    for rel in [*man["system_prompt"], man["tools"], man["guardrails"], man["config"]]:
        assert (ROOT / rel).is_file() and ".." not in rel
    tools = {t["function"]["name"]: t for t in yaml.safe_load((ROOT / man["tools"]).read_text())}
    assert {"email_send", "gmail_send", "slack_post", "outlook_calendar_create", "start_task"} <= set(tools)
    assert tools["gmail_send"]["x-tandem"]["policy"] == "ask"
    assert tools["memory_search"]["x-tandem"]["policy"] == "allow"
    guard = yaml.safe_load((ROOT / man["guardrails"]).read_text())
    assert any("sk-" in g["pattern"] for g in guard["forbidden_patterns"])
    agent = yaml.safe_load((ROOT / man["config"]).read_text())
    assert agent["agent"] == "tandem" and agent["max_turns"] > 0


def test_harness_export_cli(tmp_path: Path) -> None:
    from tandem.cli import main

    (tmp_path / "harness.yaml").write_text("agent: tandem\n")
    assert main(["harness", "export", "--root", str(tmp_path), "--check"]) == 1
    assert main(["harness", "export", "--root", str(tmp_path)]) == 0
    assert main(["harness", "export", "--root", str(tmp_path), "--check"]) == 0
    assert (tmp_path / "harness" / "tools.yaml").read_text().startswith("# Generated")
