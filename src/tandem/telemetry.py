"""OpenTelemetry GenAI traces over OTLP/HTTP JSON, with no SDK dependency (SPEC §15.6). Off by default.

One `invoke_agent tandem` root span per triage batch, brief, task or chat reply; a `chat {model}` span per
model call; an `execute_tool {name}` span per tool call. Attribute names follow the OpenTelemetry GenAI
semantic conventions, so any OTLP backend (and ambientq) can read them. Message content passes the
`Redactor` before it is queued, and export never blocks or fails the agent: spans that can't be sent are
dropped with a log line.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import time
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from tandem import __version__

if TYPE_CHECKING:
    from tandem.config import TelemetryConfig
    from tandem.models.base import Message, ModelResponse

log = logging.getLogger("tandem.telemetry")

AGENT_NAME = "tandem"
KIND_INTERNAL, KIND_CLIENT = 1, 3
STATUS_OK, STATUS_ERROR = 1, 2
PROVIDER_NAMES = {  # gen_ai.provider.name well-known values
    "anthropic": "anthropic",
    "openai": "openai",
    "gemini": "gcp.gemini",
    "openai_compatible": "openai",  # same wire protocol (NAI, vLLM, LiteLLM...); the base URL says where
    "ollama": "ollama",
    "mock": "tandem.mock",
}
MAX_ATTR = 32_000  # characters per content attribute


@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str
    parent_id: str | None = None
    kind: int = KIND_INTERNAL
    start_ns: int = field(default_factory=time.time_ns)
    end_ns: int = 0
    attributes: dict[str, Any] = field(default_factory=dict)
    status: int = 0
    status_message: str = ""

    def set(self, key: str, value: Any) -> None:
        if value is not None:
            self.attributes[key] = value

    def error(self, exc: BaseException | str) -> None:
        self.status = STATUS_ERROR
        self.status_message = str(exc)[:500]
        self.attributes.setdefault(
            "error.type", type(exc).__name__ if isinstance(exc, BaseException) else "error"
        )


_current: ContextVar[Span | None] = ContextVar("tandem_span", default=None)


class Tracer:
    """Creates spans and ships them in batches. `NOOP` (disabled) costs one attribute check per call site."""

    def __init__(
        self,
        endpoint: str | None = None,
        *,
        enabled: bool = True,
        service_name: str = "tandem",
        headers: dict[str, str] | None = None,
        capture_content: bool = True,
        redact: Callable[[str], str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_queue: int = 5000,
        resource: dict[str, Any] | None = None,
    ) -> None:
        self.enabled = enabled and bool(endpoint)
        self.endpoint = endpoint
        self.service_name = service_name
        self.headers = headers or {}
        self.capture_content = capture_content
        self.redact = redact or (lambda s: s)
        self.transport = transport
        self.max_queue = max_queue
        self.resource = resource or {}
        self.queue: list[Span] = []
        self.exported = 0
        self.dropped = 0
        self._task: asyncio.Task[None] | None = None

    # ---- spans ---------------------------------------------------------------------------------------------
    @contextlib.contextmanager
    def span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
        *,
        kind: int = KIND_INTERNAL,
        root: bool = False,
    ) -> Iterator[Span | None]:
        """A span around a block; nested spans become its children. `root=True` starts a new trace."""
        if not self.enabled:
            yield None
            return
        parent = None if root else _current.get()
        sp = Span(
            name,
            parent.trace_id if parent else secrets.token_hex(16),
            secrets.token_hex(8),
            parent.span_id if parent else None,
            kind,
        )
        if parent and "gen_ai.conversation.id" in parent.attributes:
            sp.set("gen_ai.conversation.id", parent.attributes["gen_ai.conversation.id"])
        for k, v in (attributes or {}).items():
            sp.set(k, v)
        token = _current.set(sp)
        try:
            yield sp
        except BaseException as exc:
            sp.error(exc)
            raise
        finally:
            _current.reset(token)
            sp.end_ns = time.time_ns()
            if sp.status == 0:
                sp.status = STATUS_OK
            self._enqueue(sp)

    def agent_span(
        self, run_kind: str, conversation_id: str, **attrs: Any
    ) -> contextlib.AbstractContextManager:
        """The root of one unit of agent work (a triage batch, a brief, a task, a chat reply)."""
        return self.span(
            f"invoke_agent {AGENT_NAME}",
            {
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": AGENT_NAME,
                "gen_ai.conversation.id": conversation_id,
                "tandem.run.kind": run_kind,
                **attrs,
            },
            root=True,
        )

    def _enqueue(self, sp: Span) -> None:
        if len(self.queue) >= self.max_queue:
            self.dropped += 1
            return
        self.queue.append(sp)

    # ---- content -------------------------------------------------------------------------------------------
    def content(self, value: Any) -> str | None:
        if not self.capture_content:
            return None
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        text = self.redact(text)
        return text if len(text) <= MAX_ATTR else text[: MAX_ATTR - 20] + "...[truncated]"

    def record_model_call(
        self, sp: Span | None, messages: list[Message], resp: ModelResponse | None, tools: list[Any] | None
    ) -> None:
        if sp is None:
            return
        system = [m.content for m in messages if m.role == "system"]
        if system:
            sp.set(
                "gen_ai.system_instructions", self.content([{"type": "text", "content": s} for s in system])
            )
        sp.set(
            "gen_ai.input.messages",
            self.content([_semconv_message(m) for m in messages if m.role != "system"]),
        )
        if tools:
            sp.set(
                "gen_ai.tool.definitions", self.content([{"type": "function", "name": t.name} for t in tools])
            )
        if resp is None:
            return
        parts: list[dict[str, Any]] = []
        if resp.text:
            parts.append({"type": "text", "content": resp.text})
        for tc in resp.tool_calls:
            parts.append({"type": "tool_call", "id": tc.id, "name": tc.name, "arguments": tc.args})
        sp.set(
            "gen_ai.output.messages",
            self.content(
                [{"role": "assistant", "parts": parts, "finish_reason": resp.stop_reason or "stop"}]
            ),
        )
        sp.set("gen_ai.response.model", resp.model)
        sp.set("gen_ai.response.finish_reasons", [resp.stop_reason or "stop"])
        sp.set("gen_ai.usage.input_tokens", resp.usage.input_tokens)
        sp.set("gen_ai.usage.output_tokens", resp.usage.output_tokens)

    # ---- export --------------------------------------------------------------------------------------------
    def payload(self, spans: list[Span]) -> dict[str, Any]:
        """An OTLP `ExportTraceServiceRequest` in its JSON encoding."""
        resource = {"service.name": self.service_name, "service.version": __version__, **self.resource}
        return {
            "resourceSpans": [
                {
                    "resource": {"attributes": _attrs(resource)},
                    "scopeSpans": [
                        {
                            "scope": {"name": "tandem", "version": __version__},
                            "spans": [_span_json(s) for s in spans],
                        }
                    ],
                }
            ]
        }

    async def flush(self) -> int:
        if not self.queue or not self.endpoint:
            return 0
        batch, self.queue = self.queue[:500], self.queue[500:]
        try:
            async with httpx.AsyncClient(timeout=10, transport=self.transport) as client:
                r = await client.post(
                    self.endpoint,
                    json=self.payload(batch),
                    headers={"Content-Type": "application/json", **self.headers},
                )
                r.raise_for_status()
            self.exported += len(batch)
        except Exception as exc:
            self.dropped += len(batch)
            log.warning("telemetry export to %s failed, dropped %d spans: %s", self.endpoint, len(batch), exc)
            return 0
        return len(batch)

    def start(self, every_s: float = 2.0) -> None:
        if not self.enabled or self._task:
            return

        async def loop() -> None:
            while True:
                await asyncio.sleep(every_s)
                while await self.flush():
                    pass

        self._task = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        while self.queue and await self.flush():
            pass


NOOP = Tracer(enabled=False)
_tracer: Tracer = NOOP


def tracer() -> Tracer:
    return _tracer


def set_tracer(t: Tracer | None) -> None:
    global _tracer
    _tracer = t or NOOP


def from_config(
    cfg: TelemetryConfig, redact: Callable[[str], str], env: dict[str, str] | None = None
) -> Tracer:
    env = env if env is not None else dict(os.environ)
    endpoint = cfg.endpoint or env.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if not endpoint and env.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        endpoint = env["OTEL_EXPORTER_OTLP_ENDPOINT"].rstrip("/") + "/v1/traces"
    headers: dict[str, str] = {}
    raw = env.get(cfg.headers_env) if cfg.headers_env else env.get("OTEL_EXPORTER_OTLP_HEADERS")
    for pair in (raw or "").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            headers[k.strip()] = v.strip()
    switch = env.get("TANDEM_TELEMETRY", "").strip().lower()
    enabled = cfg.enabled or switch in ("1", "true", "yes", "on")
    return Tracer(
        endpoint,
        enabled=enabled and switch not in ("0", "false", "no", "off"),
        service_name=cfg.service_name,
        headers=headers,
        capture_content=cfg.capture_content,
        redact=redact,
        resource={"ambientq.team": cfg.team} if cfg.team else None,
    )


# ---- encoding ----------------------------------------------------------------------------------------------


def _semconv_message(m: Message) -> dict[str, Any]:
    """A Tandem message in the GenAI semconv `parts` shape."""
    if m.role == "tool":
        return {
            "role": "tool",
            "parts": [{"type": "tool_call_response", "id": m.tool_call_id, "response": m.content}],
        }
    parts: list[dict[str, Any]] = []
    if m.content:
        parts.append({"type": "text", "content": m.content})
    for tc in m.tool_calls:
        parts.append({"type": "tool_call", "id": tc.id, "name": tc.name, "arguments": tc.args})
    return {"role": m.role, "parts": parts}


def _value(v: Any) -> dict[str, Any]:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, list | tuple):
        return {"arrayValue": {"values": [_value(x) for x in v]}}
    if isinstance(v, dict):
        return {"stringValue": json.dumps(v, ensure_ascii=False, default=str)}
    return {"stringValue": str(v)}


def _attrs(d: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"key": k, "value": _value(v)} for k, v in d.items() if v is not None]


def _span_json(s: Span) -> dict[str, Any]:
    out: dict[str, Any] = {
        "traceId": s.trace_id,
        "spanId": s.span_id,
        "name": s.name,
        "kind": s.kind,
        "startTimeUnixNano": str(s.start_ns),
        "endTimeUnixNano": str(s.end_ns or s.start_ns),
        "attributes": _attrs(s.attributes),
        "status": {"code": s.status, **({"message": s.status_message} if s.status_message else {})},
    }
    if s.parent_id:
        out["parentSpanId"] = s.parent_id
    return out
