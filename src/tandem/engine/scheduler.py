"""The heartbeat (SPEC §7.1): check-ins, the daily brief, and approval expiry. `due()` is pure."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from tandem.util import iso, parse_iso

log = logging.getLogger("tandem.scheduler")
EXPIRY_EVERY = timedelta(seconds=15)


def due(
    now: datetime, state: dict[str, Any], checkin_every: timedelta, brief_time: Any, tz: str
) -> list[str]:
    """Which jobs should run at `now`, given the last runs in `state`."""
    jobs = []
    last_checkin = parse_iso(state.get("last_checkin"))
    if last_checkin is None or now - last_checkin >= checkin_every:
        jobs.append("checkin")
    local = now.astimezone(ZoneInfo(tz))
    today = local.date().isoformat()
    brief_at = local.replace(hour=brief_time.hour, minute=brief_time.minute, second=0, microsecond=0)
    if local >= brief_at and state.get("last_brief_date") != today:
        jobs.append("brief")
    last_exp = parse_iso(state.get("last_expiry"))
    if last_exp is None or now - last_exp >= EXPIRY_EVERY:
        jobs.append("expire_approvals")
    return jobs


class Scheduler:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self._task: asyncio.Task[None] | None = None
        self.running = False

    def state(self) -> dict[str, Any]:
        s = self.rt.store
        return {
            "last_checkin": s.kv_get("last_checkin"),
            "last_brief_date": s.kv_get("last_brief_date"),
            "last_expiry": s.kv_get("last_expiry"),
        }

    async def tick(self, now: datetime | None = None) -> list[str]:
        rt = self.rt
        now = now or rt.clock.now()
        sched = rt.cfg.schedule
        jobs = due(now, self.state(), sched.checkin_interval, sched.brief_time, sched.timezone)
        for job in jobs:
            try:
                if job == "checkin":
                    rt.store.kv_set("last_checkin", iso(now))
                    await rt.checkin(now)
                elif job == "brief":
                    rt.store.kv_set(
                        "last_brief_date", now.astimezone(ZoneInfo(sched.timezone)).date().isoformat()
                    )
                    await rt.brief.run(now)
                elif job == "expire_approvals":
                    rt.store.kv_set("last_expiry", iso(now))
                    for ap in rt.approvals.expire_due():
                        rt.store.add_activity(
                            "approval",
                            f"Expired without an answer (denied): {ap['title']}",
                            None,
                            ap.get("task_id"),
                            "warn",
                        )
            except Exception as exc:
                log.exception("job %s failed", job)
                rt.store.add_activity("scheduler", f"Job failed: {job}", str(exc)[:300], level="error")
        return jobs

    async def _loop(self) -> None:
        while self.running:
            await self.tick()
            await asyncio.sleep(1.0)

    def start(self) -> None:
        if not self.running:
            self.running = True
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
