"""The connector contract (SPEC §3.9) and the registry of connector types."""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, ClassVar

from tandem.tools import Tool
from tandem.util import Clock, iso


@dataclass
class InboundEvent:
    source: str
    kind: str  # email | calendar | payment | webhook | file | mcp | ...
    external_id: str
    title: str
    body: str = ""
    sender: str | None = None
    occurred_at: datetime | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def row(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "kind": self.kind,
            "external_id": self.external_id,
            "title": self.title[:500],
            "body": self.body[:20000],
            "sender": self.sender,
            "occurred_at": iso(self.occurred_at),
            "data": self.data,
        }


@dataclass
class CalendarItem:
    title: str
    start: datetime
    end: datetime | None = None
    location: str | None = None
    attendees: list[str] = field(default_factory=list)
    source: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "start": iso(self.start),
            "end": iso(self.end),
            "location": self.location,
            "attendees": self.attendees,
            "source": self.source,
        }


@dataclass
class PushRequest:
    """What a webhook delivered: any body (possibly empty), lower-cased headers, query parameters."""

    payload: dict[str, Any]
    headers: dict[str, str]
    raw: bytes = b""
    query: dict[str, str] = field(default_factory=dict)


@dataclass
class PushResult:
    """New events, and optionally the exact HTTP answer a handshake needs (Slack challenge, Graph token)."""

    events: list[InboundEvent] = field(default_factory=list)
    status: int = 200
    body: str | dict[str, Any] | None = None
    content_type: str = "application/json"


class Connector(ABC):  # noqa: B024 - every method has a sensible default
    type: ClassVar[str] = "base"
    send_tool: str | None = None  # a tool that can send a drafted reply (used by the brief)

    def __init__(self, name: str, options: dict[str, Any], clock: Clock, secrets: Any = None) -> None:
        self.name = name
        self.options = options
        self.clock = clock
        self.secrets = secrets
        self.last_error: str | None = None
        self.last_poll: datetime | None = None

    @property
    def poll_every(self) -> timedelta | None:
        from tandem.config import parse_duration

        val = self.options.get("poll_every")
        return parse_duration(val) if val else None

    async def start(self) -> None:
        return None

    async def poll(self, since: datetime | None) -> list[InboundEvent]:
        return []

    def tools(self) -> list[Tool]:
        return []

    async def handle_webhook(
        self, payload: dict[str, Any], headers: dict[str, str], raw: bytes = b""
    ) -> list[InboundEvent]:
        raise NotImplementedError(f"connector {self.name!r} does not accept webhooks")

    async def handle_push(self, req: PushRequest) -> PushResult:
        """Webhook entry point (SPEC §15.3). Override to answer handshakes; the default is v0.1's hook."""
        return PushResult(await self.handle_webhook(req.payload, req.headers, req.raw))

    async def upcoming(self, start: datetime, end: datetime) -> list[CalendarItem]:
        return []

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "status": "error" if self.last_error else "ok",
            "error": self.last_error,
            "last_poll": iso(self.last_poll),
            "tools": [t.name for t in self.tools()],
        }

    async def close(self) -> None:
        return None


CONNECTOR_TYPES: dict[str, type[Connector]] = {}


def register_connector(cls: type[Connector]) -> type[Connector]:
    CONNECTOR_TYPES[cls.type] = cls
    return cls
