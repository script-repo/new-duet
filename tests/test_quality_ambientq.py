"""Tandem's traces graded by ambientq (SPEC §15.6, docs/QUALITY.md).

Runs only where ambientq is installed: `uv sync --extra quality`, or `uv pip install -e <checkout>`.
A clean mock day must produce no findings; a planted bad chat turn (an unknown tool, schema-invalid
arguments, a failed tool the answer glosses over) must come back as findings and insights.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from tandem.config import TelemetryConfig
from tandem.models.base import ModelResponse, ToolCall
from tandem.runtime import Runtime
from tests.conftest import mock_cfg

ambientq = pytest.importorskip("ambientq", reason="ambientq not installed (uv sync --extra quality)")
from ambientq.config import Config  # noqa: E402
from ambientq.evaluate import evaluate  # noqa: E402
from ambientq.harness import Harness  # noqa: E402
from ambientq.ingest.otlp import decode_export_request  # noqa: E402
from ambientq.store import open_store  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


async def traced_day(make_rt: Callable[..., Runtime], scripted: list[ModelResponse] | None) -> list[dict]:
    """A mock check-in plus one chat turn; returns the OTLP JSON requests Tandem exported."""
    cfg = mock_cfg()
    cfg.telemetry = TelemetryConfig(enabled=True, endpoint="http://ambientq.test/v1/traces")
    rt = make_rt(cfg=cfg)
    exported: list[dict[str, Any]] = []

    def collect(req: httpx.Request) -> httpx.Response:
        exported.append(json.loads(req.content))
        return httpx.Response(200, json={"partialSuccess": {}})

    rt.tracer.transport = httpx.MockTransport(collect)
    if scripted is not None:
        replies = iter(scripted)
        chat_model = rt.models.for_role("chat")

        async def script(*_a: Any, **_k: Any) -> ModelResponse:
            return next(replies)

        chat_model._complete_retrying = script  # type: ignore[method-assign]
    await rt.start()
    try:
        await rt.checkin()
        text = "Pay the plumber and tell me what's in today's mail."
        rt.store.add_message("user", text)
        await rt.chat.reply(text)
    finally:
        await rt.stop()
    assert exported, "telemetry exported nothing"
    return exported


def grade(exported: list[dict]) -> tuple[Any, Any, Any]:
    store = open_store("sqlite:///:memory:")
    for payload in exported:
        store.ingest_spans(decode_export_request(payload))
    harness = Harness.load(ROOT)
    result = evaluate(store, Config.load(None), harness, None)  # deterministic checks; no judge model
    return store, harness, result


async def test_clean_mock_traces_reconstruct_and_pass(make_rt: Callable[..., Runtime]) -> None:
    store, harness, result = grade(await traced_day(make_rt, None))
    assert harness.agent == "tandem" and "gmail_send" in harness.tool_names()
    convs = store.list_conversations()
    assert {c.agent_name for c in convs} == {"tandem"}
    ids = {c.conversation_id.split("_")[0] for c in convs}
    assert {"tri", "chat"} <= ids, "one conversation per triage batch and per chat reply"
    chat = next(c for c in convs if c.conversation_id.startswith("chat_"))
    roles = [t.role for t in chat.turns]
    assert "user" in roles and "tool" in roles and roles[-1] == "assistant"  # history, the ask, tools, answer
    assert result.run.candidates == len(convs)
    assert [f.actual for f in result.findings] == [], "a clean mock day has no findings"


async def test_planted_defects_come_back_as_findings_and_insights(make_rt: Callable[..., Runtime]) -> None:
    scripted = [
        ModelResponse(
            text="",
            tool_calls=[
                ToolCall("call_1", "wire_money", {"to": "plumber", "amount": 400}),
                ToolCall("call_2", "email_search", {"q": "today", "folder": "inbox"}),
            ],
            model="mock-strong",
            stop_reason="tool_use",
        ),
        ModelResponse(text="Done! The plumber is paid and your inbox is clear.", model="mock-strong"),
    ]
    store, _, result = grade(await traced_day(make_rt, scripted))
    found = {(f.category, f.tool_name) for f in result.findings if f.verified is not False}
    assert ("tool_selection", "wire_money") in found, found  # unknown_tool
    assert ("tool_arguments", "email_search") in found, (
        found
    )  # tool_args_schema: query missing, folder unknown
    assert ("tool_results", "wire_money") in found, found  # tool_error_ignored: "Done!" hides the failure
    assert all(f.conversation_id.startswith("chat_") for f in result.findings)
    insights = store.insights()
    assert insights, "findings are correlated into insights a person can act on"
    assert result.run.findings >= 3
