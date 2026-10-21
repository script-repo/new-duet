"""The Runtime wires every component together; the API, the CLI and the tests all drive this object."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from tandem import __version__
from tandem.artifacts import Artifacts
from tandem.bus import EventBus
from tandem.config import AppConfig, Settings, examples_dir, load_config, policy_path
from tandem.connectors import Connector, build_connectors
from tandem.connectors.base import InboundEvent
from tandem.connectors.fake import FakeConnector
from tandem.engine.brief import BriefBuilder
from tandem.engine.builtin_tools import builtin_tools
from tandem.engine.chat import ChatAgent
from tandem.engine.notify import NotificationPolicy, Notifier, apply_notify_settings
from tandem.engine.scheduler import Scheduler
from tandem.engine.tasks import TaskManager
from tandem.engine.triage import Triage
from tandem.harness import describe as describe_harnesses
from tandem.harness import load_plugins
from tandem.memory import Memory, Skills
from tandem.models import ModelRouter
from tandem.models.base import Message
from tandem.policy import Policy
from tandem.push import TTL_TASK, PushService, approval_actions
from tandem.sandbox import SandboxFactory
from tandem.security import Redactor, SecretResolver
from tandem.store import Store
from tandem.tools import ApprovalService, ToolContext, ToolRegistry
from tandem.util import Clock, iso
from tandem.vault import Cipher, TokenVault, load_server_key

log = logging.getLogger("tandem")


class Runtime:
    def __init__(
        self,
        settings: Settings | None = None,
        cfg: AppConfig | None = None,
        clock: Clock | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.cfg = cfg or load_config(self.settings)
        self.clock = clock or Clock()
        self.bus = EventBus()
        self.store = Store(self.settings.db_url, self.bus, self.clock)
        self.secrets = SecretResolver(env)
        self.redactor = Redactor(self.secrets)
        self.cipher = Cipher(load_server_key(self.settings.state_dir, env))
        self.vault = TokenVault(self.store, self.cipher, self.redactor)
        self.policy = Policy.load(policy_path(self.cfg))
        self.approvals = ApprovalService(self.store, self.policy)
        self.registry = ToolRegistry(self.store, self.policy, self.approvals, self.redactor)
        self.models = ModelRouter(self.cfg, transport)
        self.memory = Memory(self.store, self.cfg.memory.core_chars)
        skill_dirs = [
            examples_dir() / "skills",
            self.settings.state_dir / "skills",
            *[Path(p) for p in self.cfg.skills],
        ]
        self.skills = Skills(skill_dirs)
        self.artifacts = Artifacts(self.store, self.settings.state_dir / "artifacts")
        self.sandboxes = SandboxFactory(self.settings.state_dir / "sandboxes", self.cfg.sandbox)
        self.connectors: dict[str, Connector] = build_connectors(
            self.cfg.connectors, self.clock, self.secrets, self.cfg.schedule.timezone
        )
        apply_notify_settings(self.cfg.notify, self.store.kv_get("notify_settings") or {})
        self.push = PushService(self, env)
        self.notify_policy = NotificationPolicy(self.cfg.notify, self.cfg.schedule.timezone, self.store)
        self.notifier = Notifier(self.cfg.notify, self.store, self.push)
        self.triage = Triage(self)
        self.brief = BriefBuilder(self)
        self.tasks = TaskManager(self)
        self.chat = ChatAgent(self)
        self.scheduler = Scheduler(self)
        self.harness_options: dict[str, Any] = dict(getattr(self.cfg, "harness_options", {}) or {})
        self._bg: set[asyncio.Task[Any]] = set()
        self.started = False
        load_plugins()
        self.registry.extend(builtin_tools())
        self.approvals.on_created.append(self._on_approval)
        self.approvals.on_edit.append(self._on_edit)

    # ---- lifecycle ------------------------------------------------------------------------------------------
    async def start(self, run_scheduler: bool = False) -> None:
        if self.started:
            return
        self.started = True
        self.approvals.cancel_orphans()
        for c in self.connectors.values():
            try:
                await c.start()
            except Exception as exc:
                c.last_error = str(exc)
                log.warning("connector %s failed to start: %s", c.name, exc)
            self.registry.extend(c.tools())
        self._seed_demo()
        if not self.store.list_messages(limit=1):
            name = f" {self.cfg.user_name}" if self.cfg.user_name else ""
            self.store.add_message(
                "agent",
                f"Hi{name}, I'm Tandem. I'll keep an eye on your apps, brief you each morning, "
                "and only reach out when something needs you. Ask me for anything here.",
            )
        if self.cfg.demo_autoplay and self.store.latest_brief() is None:
            await self.checkin()
            await self.brief.run()
            self.store.kv_set("last_checkin", iso(self.clock.now()))
        if run_scheduler:
            self.scheduler.start()

    async def stop(self) -> None:
        await self.scheduler.stop()
        for t in list(self.tasks._running.values()):
            t.cancel()
        for t in list(self._bg):
            t.cancel()
        for c in self.connectors.values():
            try:
                await c.close()
            except Exception:
                pass
        self.started = False

    def _seed_demo(self) -> None:
        fakes = [c for c in self.connectors.values() if isinstance(c, FakeConnector)]
        if not fakes:
            return
        fake = fakes[0]
        if not self.cfg.user_name:
            self.cfg.user_name = fake.user().get("name", "")
        if not self.store.list_memory():
            for m in fake.seed_memory():
                self.memory.write(m["content"], m.get("kind", "fact"), m.get("key"), source="demo")

    def background(self, coro_or_task: Any) -> asyncio.Task[Any]:
        task = coro_or_task if isinstance(coro_or_task, asyncio.Task) else asyncio.create_task(coro_or_task)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return task

    # ---- approvals ---------------------------------------------------------------------------------------------
    async def _on_approval(self, ap: dict[str, Any], ctx: ToolContext) -> None:
        if ctx.origin == "brief":
            return  # the brief posts its drafts after the brief card
        msg = self.store.add_message(
            "agent", f"Needs your OK: {ap['title']}", {"type": "approval", "ref": ap["id"]}
        )
        if not self.notify_policy.quiet_now(self.clock.now()):
            await self.notifier.push(
                "Needs your OK",
                ap["title"],
                "high",
                msg["id"],
                approval_id=ap["id"],
                tag=f"tandem-approval-{ap['id']}",
                actions=approval_actions(ap.get("tags")),
                ttl=int(self.policy.timeout_s),  # a card that expired is not worth delivering
            )

    async def _on_edit(self, ap: dict[str, Any], new_args: dict[str, Any]) -> None:
        """Learn from the user's edit to a draft (reflection, after LangGraph's EAIA)."""
        edited = {k: v for k, v in new_args.items() if ap["args"].get(k) != v}
        if not edited:
            return
        payload = {"kind": "reflect", "tool": ap["tool"], "original": ap["args"], "edited": edited}
        try:
            out = await self.models.for_role("chat").complete_json(
                [
                    Message(
                        "system",
                        "The user edited a draft before approving it. State the durable preference this shows, "
                        'in one sentence. Reply with JSON {"preference": "..."}',
                    ),
                    Message(
                        "user", "<data>" + json.dumps(payload, ensure_ascii=False, default=str) + "</data>"
                    ),
                ]
            )
            pref = str(out.get("preference") or "").strip()
            if pref:
                self.memory.write(pref, "preference", source="reflection")
                self.store.add_activity("memory", "Learned a preference from your edit", pref)
        except Exception as exc:
            log.info("reflection skipped: %s", exc)

    async def propose_draft(
        self, tool: str, args: dict[str, Any], origin: str = "brief"
    ) -> dict[str, Any] | None:
        """Start a gated call in the background and return its approval card as soon as it exists."""
        loop = asyncio.get_running_loop()
        got: asyncio.Future[dict[str, Any]] = loop.create_future()

        def on_approval(ap: dict[str, Any]) -> None:
            if not got.done():
                got.set_result(ap)

        ctx = ToolContext(origin=origin, rt=self, on_approval=on_approval)
        task = self.background(self.registry.invoke(tool, args, ctx))
        _done, _ = await asyncio.wait({got, task}, return_when=asyncio.FIRST_COMPLETED, timeout=10)
        return got.result() if got.done() else None

    # ---- tasks -----------------------------------------------------------------------------------------------
    async def post_task_done(self, task: dict[str, Any]) -> None:
        ok = task["state"] == "done"
        text = (
            (task.get("summary") or "Done.")
            if ok
            else f"I couldn't finish “{task['title']}”: {task.get('error') or ''}"
        )
        # The live task card posted at start already shows state and logs; the done message carries the
        # summary and each deliverable as its own card.
        arts = (
            [a for aid in task.get("artifact_ids", []) if (a := self.store.get_artifact(aid))] if ok else []
        )
        msg = self.store.add_message(
            "agent", text, {"type": "artifact", "ref": arts[0]["id"]} if arts else None
        )
        for art in arts[1:]:
            self.store.add_message("agent", art["title"], {"type": "artifact", "ref": art["id"]})
        if not self.notify_policy.quiet_now(self.clock.now()):
            await self.notifier.push(
                "Done" if ok else "Task failed", task["title"], "normal", msg["id"], ttl=TTL_TASK
            )

    def update_notify_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        """`PUT /api/settings/notify`: stored in kv over tandem.yaml, applied to the live config at once."""
        stored = {**(self.store.kv_get("notify_settings") or {}), **changes}
        apply_notify_settings(self.cfg.notify, stored)
        self.store.kv_set("notify_settings", stored)
        return self.notify_settings()

    def notify_settings(self) -> dict[str, Any]:
        n = self.cfg.notify
        return {
            "min_urgency": n.min_urgency,
            "quiet_hours": n.quiet_hours,
            "max_per_hour": n.max_per_hour,
            "push_enabled": n.push_enabled,
        }

    # ---- check-ins -------------------------------------------------------------------------------------------
    async def checkin(self, now: datetime | None = None) -> dict[str, int]:
        now = now or self.clock.now()
        catchup = timedelta(minutes=5)
        new: list[dict[str, Any]] = []
        for c in self.connectors.values():
            if c.type == "webhook":
                continue
            since = (c.last_poll - catchup) if c.last_poll else None
            try:
                events = await c.poll(since)
                c.last_error = None
            except Exception as exc:
                c.last_error = str(exc)[:300]
                self.store.add_activity("checkin", f"Couldn't check {c.name}", c.last_error, level="warn")
                continue
            new += [row for row in (self.store.add_event(e.row()) for e in events) if row]
        result = await self._process(new, now)
        self.store.add_activity(
            "checkin",
            f"Checked in: {result['new_events']} new",
            f"{result['notified']} notified, {result['ignored']} ignored, {result['acted']} handled, "
            f"{result['held']} saved for the brief",
        )
        if new:
            self.background(self.chat.summarize_old())
        return result

    async def ingest(self, connector: str, events: list[InboundEvent]) -> dict[str, int]:
        """Event trigger (webhooks): triage right away instead of waiting for the next check-in."""
        rows = [row for row in (self.store.add_event(e.row()) for e in events) if row]
        result = await self._process(rows, self.clock.now())
        self.store.add_activity(
            "webhook", f"{connector}: {len(rows)} new event(s)", f"{result['notified']} notified"
        )
        return result

    async def _process(self, events: list[dict[str, Any]], now: datetime) -> dict[str, int]:
        counts = {"new_events": len(events), "notified": 0, "ignored": 0, "acted": 0, "held": 0}
        if not events:
            return counts
        verdicts = await self.triage.run(events)
        by_id = {e["id"]: e for e in events}
        coalesced: list[str] = []
        for v in verdicts:
            ev = by_id[v.event_id]
            triage = {"reason": v.reason, "action": v.action, "source": v.source}
            if v.action == "ignore":
                self.store.update_event(ev["id"], status="ignored", urgency=v.urgency, triage=triage)
                counts["ignored"] += 1
                self.store.add_activity("triage", f"Ignored: {ev['title']}", v.reason)
            elif v.action == "act" and v.task:
                self.store.update_event(ev["id"], status="acted", urgency=v.urgency, triage=triage)
                counts["acted"] += 1
                self.store.add_activity("triage", f"Handling: {ev['title']}", v.reason, level="info")
                try:
                    await self.tasks.start(
                        str(v.task.get("instructions") or ev["title"]),
                        title=v.task.get("title"),
                        origin="triage",
                    )
                except ValueError as exc:
                    self.store.add_activity("triage", "Couldn't start a task", str(exc), level="warn")
            elif v.action == "notify":
                decision = self.notify_policy.decide(v.urgency, now)
                if decision == "hold":
                    self.store.update_event(ev["id"], status="brief", urgency=v.urgency, triage=triage)
                    counts["held"] += 1
                    self.store.add_activity(
                        "triage",
                        f"Saved for your brief: {ev['title']}",
                        f"{v.urgency}: {v.reason} (quiet hours)",
                    )
                    continue
                self.store.update_event(ev["id"], status="notified", urgency=v.urgency, triage=triage)
                counts["notified"] += 1
                self.store.add_activity("notify", f"Told you: {ev['title']}", v.reason, level="notify")
                if decision == "push":
                    text = (
                        f"{ev['title']}"
                        + (f" ({ev['sender']})" if ev.get("sender") else "")
                        + (f"\n{v.reason}" if v.reason else "")
                    )
                    msg = self.store.add_message(
                        "agent", text, {"type": "notification", "ref": ev["id"], "urgency": v.urgency}
                    )
                    await self.notifier.push(
                        ev["title"], v.reason or ev.get("sender") or "", v.urgency, msg["id"]
                    )
                else:
                    coalesced.append(ev["title"])
            else:
                self.store.update_event(ev["id"], status="brief", urgency=v.urgency, triage=triage)
                counts["held"] += 1
                self.store.add_activity("triage", f"For your brief: {ev['title']}", v.reason)
        if coalesced:
            text = f"{len(coalesced)} more things need you: " + "; ".join(coalesced[:5])
            msg = self.store.add_message(
                "agent", text, {"type": "notification", "ref": "", "urgency": "high"}
            )
            await self.notifier.push("More things need you", text, "high", msg["id"])
        return counts

    # ---- views -----------------------------------------------------------------------------------------------
    async def upcoming(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        items = []
        for c in self.connectors.values():
            try:
                items += [i.public() for i in await c.upcoming(start, end)]
            except Exception as exc:
                c.last_error = str(exc)[:300]
        return sorted(items, key=lambda i: i["start"] or "")

    def local_midnight(self, now: datetime | None = None) -> datetime:
        local = (now or self.clock.now()).astimezone(ZoneInfo(self.cfg.schedule.timezone))
        return local.replace(hour=0, minute=0, second=0, microsecond=0)

    async def today(self) -> dict[str, Any]:
        now = self.clock.now()
        midnight = self.local_midnight(now)
        since = iso(midnight) or ""
        brief = self.store.latest_brief()
        local_date = midnight.date().isoformat()
        events_today = [e for e in self.store.list_events(limit=500) if e["created_at"] >= since]
        done = [
            t
            for t in self.store.list_tasks(state=["done", "failed"], limit=50)
            if (t.get("finished_at") or "") >= since
        ]
        return {
            "date": local_date,
            "brief": brief,  # the latest brief (the UI marks it as older when its date isn't today)
            "needs_you": self.store.list_approvals(state="pending"),
            "in_progress": self.store.list_tasks(state=["queued", "running", "waiting"]),
            "done": done,
            "upcoming": await self.upcoming(now, midnight + timedelta(days=1)),
            "notifications": [e for e in events_today if e["status"] == "notified"],
            "stats": {
                "events_today": len(events_today),
                "ignored": len([e for e in events_today if e["status"] == "ignored"]),
                "notified": len([e for e in events_today if e["status"] == "notified"]),
                "tasks_done": len([t for t in done if t["state"] == "done"]),
            },
        }

    def outbox(self) -> list[dict[str, Any]]:
        out = []
        for c in self.connectors.values():
            out += [{"connector": c.name, **o} for o in getattr(c, "outbox", [])]
        return out

    def public_config(self) -> dict[str, Any]:
        cfg = self.cfg
        return {
            "version": __version__,
            "mode": "mock" if cfg.mock else "live",
            "timezone": cfg.schedule.timezone,
            "user_name": cfg.user_name,
            "models": self.models.public(),
            "harnesses": describe_harnesses(cfg.harness.default),
            "connectors": [c.status() for c in self.connectors.values()],
            "policy": self.policy.public(),
            "schedule": cfg.schedule.model_dump(),
            "notify": {
                "min_urgency": cfg.notify.min_urgency,
                "quiet_hours": cfg.notify.quiet_hours,
                "max_per_hour": cfg.notify.max_per_hour,
                "channels": [c.type for c in cfg.notify.channels],
                "push": {
                    "enabled": cfg.notify.webpush_on(),
                    "subscriptions": self.push.count(),
                    "vapid_public_key": self.push.public_key,
                },
            },
            "sandbox": {
                "mode": self.sandboxes.mode,
                "image": cfg.sandbox.image,
                "network": cfg.sandbox.network,
            },
            "voice": {"server": cfg.voice.provider != "none"},
            "auth": bool(self.settings.auth_token),
        }
