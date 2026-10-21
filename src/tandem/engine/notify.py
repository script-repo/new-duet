"""Quiet until needed (SPEC §7.3): when to interrupt, and the channels that carry it."""

from __future__ import annotations

import logging
import re
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from tandem.config import NotifyConfig, parse_hhmm
from tandem.engine.triage import URGENCY
from tandem.push import TTL_DEFAULT, PushService

log = logging.getLogger("tandem.notify")


NOTIFY_SETTINGS = ("min_urgency", "quiet_hours", "max_per_hour", "push_enabled")
QUIET_HOURS = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")


def apply_notify_settings(cfg: NotifyConfig, values: dict[str, Any]) -> None:
    """Runtime settings (kv `notify_settings`) over tandem.yaml; unknown or invalid keys are ignored."""
    for key in NOTIFY_SETTINGS:
        if key not in values:
            continue
        value = values[key]
        if key == "quiet_hours" and value is not None and not QUIET_HOURS.match(str(value)):
            log.warning("ignoring invalid quiet_hours %r", value)
            continue
        try:
            setattr(cfg, key, getattr(type(cfg).model_validate({key: value}), key))
        except ValueError:
            log.warning("ignoring invalid notify setting %s=%r", key, value)


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
    """Carries what the policy decided to push: the thread (`ui`, always), Web Push, ntfy and webhooks.

    `ntfy`/`webhook` channels with `when: fallback` fire only when no Web Push delivery succeeded.
    """

    def __init__(self, cfg: NotifyConfig, store: Any, push: PushService | None = None) -> None:
        self.cfg = cfg
        self.store = store
        self.webpush = push
        self.sent: list[dict[str, Any]] = []

    async def push(
        self,
        title: str,
        body: str,
        urgency: str,
        message_id: int | None = None,
        *,
        approval_id: str | None = None,
        url: str | None = None,
        tag: str | None = None,
        actions: list[dict[str, str]] | None = None,
        ttl: int = TTL_DEFAULT,
    ) -> int:
        """Returns the number of Web Push deliveries that succeeded."""
        if tag is None:
            tag = f"tandem-{message_id}" if message_id is not None else None
        payload = {
            "title": title,
            "body": body,
            "urgency": urgency,
            "message_id": message_id,
            "approval_id": approval_id,
            "url": url or "/#/thread",
            "tag": tag,
            "actions": actions if actions is not None else [{"action": "open", "title": "Open"}],
        }
        self.store.bus.publish("notify", payload)
        delivered = 0
        if self.webpush is not None and self.cfg.webpush_on():
            try:
                delivered = await self.webpush.send(payload, urgency=urgency, ttl=ttl, topic=tag)
            except Exception as exc:  # a push failure must never break the caller (triage, approvals)
                log.warning("web push failed: %s", exc)
        self.sent.append({**payload, "webpush_delivered": delivered})
        for ch in self.cfg.channels:
            if ch.type in ("ui", "webpush") or not ch.url:
                continue
            if ch.when == "fallback" and delivered:
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
        return delivered
