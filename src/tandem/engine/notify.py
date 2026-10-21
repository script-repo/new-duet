"""Quiet until needed (SPEC §7.3): when to interrupt, and the channels that carry it."""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from tandem.config import NotifyConfig, parse_hhmm
from tandem.engine.triage import URGENCY

log = logging.getLogger("tandem.notify")


def in_quiet_hours(local: datetime, spec: str | None) -> bool:
    if not spec:
        return False
    start_s, end_s = spec.split("-")
    start, end = parse_hhmm(start_s), parse_hhmm(end_s)
    t = time(local.hour, local.minute)
    return (start <= t or t < end) if start > end else (start <= t < end)


class NotificationPolicy:
    def __init__(self, cfg: NotifyConfig, tz: str, store: Any) -> None:
        self.cfg = cfg
        self.tz = ZoneInfo(tz)
        self.store = store

    def decide(self, urgency: str, now: datetime) -> str:
        """`push` (interrupt now), `coalesce` (rate-limited: roll into one message), or `hold` (wait for the brief)."""
        if urgency == "critical":
            return self._count(now)
        if URGENCY.index(urgency) < URGENCY.index(self.cfg.min_urgency):
            return "hold"
        if in_quiet_hours(now.astimezone(self.tz), self.cfg.quiet_hours):
            return "hold"
        return self._count(now)

    def _count(self, now: datetime) -> str:
        hour = now.strftime("%Y-%m-%dT%H")
        state = self.store.kv_get("notify_counter", {}) or {}
        n = state.get(hour, 0)
        self.store.kv_set("notify_counter", {hour: n + 1})
        return "push" if n < self.cfg.max_per_hour else "coalesce"

    def quiet_now(self, now: datetime) -> bool:
        return in_quiet_hours(now.astimezone(self.tz), self.cfg.quiet_hours)


class Notifier:
    def __init__(self, cfg: NotifyConfig, store: Any) -> None:
        self.cfg = cfg
        self.store = store
        self.sent: list[dict[str, Any]] = []

    async def push(self, title: str, body: str, urgency: str, message_id: int | None = None) -> None:
        payload = {"title": title, "body": body, "urgency": urgency, "message_id": message_id}
        self.sent.append(payload)
        self.store.bus.publish("notify", payload)
        for ch in self.cfg.channels:
            if ch.type == "ui" or not ch.url:
                continue
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    if ch.type == "ntfy":
                        await client.post(
                            ch.url,
                            content=body.encode(),
                            headers={
                                "Title": title.encode("ascii", "ignore").decode(),
                                "Priority": "5" if urgency == "critical" else "4",
                            },
                        )
                    elif ch.type == "webhook":
                        await client.post(ch.url, json=payload)
            except httpx.HTTPError as exc:
                log.warning("notification channel %s failed: %s", ch.type, exc)
