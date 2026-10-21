"""Connectors: where events come from and what the agent can act on (SPEC §3.9)."""

from __future__ import annotations

from typing import Any

from tandem.config import ConnectorConfig
from tandem.connectors import calendar, fake, mail, mcp, simple  # noqa: F401 - register types
from tandem.connectors.base import (
    CONNECTOR_TYPES,
    CalendarItem,
    Connector,
    InboundEvent,
    PushRequest,
    PushResult,
    register_connector,
)
from tandem.util import Clock


def build_connectors(
    configs: list[ConnectorConfig], clock: Clock, secrets: Any, timezone: str
) -> dict[str, Connector]:
    out: dict[str, Connector] = {}
    for cfg in configs:
        if not cfg.enabled:
            continue
        cls = CONNECTOR_TYPES.get(cfg.type)
        if cls is None:
            raise ValueError(
                f"unknown connector type {cfg.type!r}; known: {', '.join(sorted(CONNECTOR_TYPES))}"
            )
        name = cfg.name or cfg.type
        if name in out:
            raise ValueError(f"two connectors are named {name!r}; give each a unique name")
        opts = cfg.options()
        opts.setdefault("timezone", timezone)
        out[name] = cls(name, opts, clock, secrets)
    return out


__all__ = [
    "CONNECTOR_TYPES",
    "CalendarItem",
    "Connector",
    "InboundEvent",
    "PushRequest",
    "PushResult",
    "build_connectors",
    "register_connector",
]
