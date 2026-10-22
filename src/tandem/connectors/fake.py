"""Fake connector with seeded demo data: an inbox, a calendar, payments and contacts that play out a day.

Nothing leaves the process: sent mail, invites and payments go to `outbox`, visible at /api/outbox.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from tandem.config import examples_dir
from tandem.connectors.base import CalendarItem, Connector, InboundEvent, register_connector
from tandem.tools import Tool, ToolContext, schema

# Values a model sends to mean "no filter".
NO_FILTER = {"", "all", "any", "*"}


def _at(day: datetime, spec: str) -> datetime:
    """`"09:10"` today, `"-22:10"` yesterday at 22:10, relative to local midnight `day`."""
    spec = str(spec)
    neg = spec.startswith("-")
    hh, mm = spec.lstrip("-").split(":")
    base = day - timedelta(days=1) if neg else day
    return base.replace(hour=int(hh), minute=int(mm))


@register_connector
class FakeConnector(Connector):
    type = "fake"
    send_tool = "email_send"

    def __init__(self, name: str, options: dict[str, Any], clock: Any, secrets: Any = None) -> None:
        super().__init__(name, options, clock, secrets)
        path = Path(options.get("data") or examples_dir() / "demo" / "cedar-street.yaml")
        self.data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        self.tz = ZoneInfo(options.get("timezone") or "UTC")
        self.outbox: list[dict[str, Any]] = []
        self.created_events: list[CalendarItem] = []

    # ---- time helpers ----------------------------------------------------------------------------------------
    def _today(self) -> datetime:
        local = self.clock.now().astimezone(self.tz)
        return local.replace(hour=0, minute=0, second=0, microsecond=0)

    def _released_inbox(self) -> list[dict[str, Any]]:
        now = self.clock.now()
        day = self._today()
        out = []
        for m in self.data.get("inbox", []):
            when = _at(day, m["at"])
            if when <= now:
                out.append({**m, "when": when, "uid": f"{m['id']}-{day.date().isoformat()}"})
        for m in self.outbox:
            if m.get("type") == "email" and m.get("reply_in_inbox"):
                out.append(m)
        return sorted(out, key=lambda x: x["when"], reverse=True)

    # ---- connector contract -------------------------------------------------------------------------------------
    async def poll(self, since: datetime | None) -> list[InboundEvent]:
        self.last_poll = self.clock.now()
        day = self._today()
        events = []
        for m in self._released_inbox():
            headers = m.get("headers") or {}
            events.append(
                InboundEvent(
                    source=self.name,
                    kind="email",
                    external_id=m["uid"],
                    title=m["subject"],
                    body=m.get("body", ""),
                    sender=m["from"],
                    occurred_at=m["when"],
                    data={"headers": headers, "message_id": m["uid"]},
                )
            )
        for p in self.data.get("payment_events", []):
            when = _at(day, p["at"])
            if when <= self.clock.now():
                events.append(
                    InboundEvent(
                        source=self.name,
                        kind="payment",
                        external_id=f"{p['id']}-{day.date()}",
                        title=p["title"],
                        body=p.get("body", ""),
                        sender="payments",
                        occurred_at=when,
                    )
                )
        return events

    async def upcoming(self, start: datetime, end: datetime) -> list[CalendarItem]:
        items = []
        for offset in range(0, max(1, (end - start).days + 2)):
            day = self._today() + timedelta(days=offset)
            for c in self.data.get("calendar", []):
                if offset > 0 and c.get("today_only", True):
                    continue
                s = _at(day, c["at"])
                item = CalendarItem(
                    title=c["title"],
                    start=s,
                    end=s + timedelta(minutes=c.get("minutes", 30)),
                    location=c.get("location"),
                    attendees=c.get("attendees", []),
                    source=self.name,
                )
                if start <= s <= end or (item.end and start <= item.end <= end):
                    items.append(item)
        items += [c for c in self.created_events if start <= c.start <= end]
        return sorted(items, key=lambda c: c.start)

    def free_slots(self, days: int = 5) -> list[str]:
        busy = {c["at"] for c in self.data.get("calendar", [])}
        slots = []
        day = self._today()
        for offset in range(1, days + 1):
            d = day + timedelta(days=offset)
            if d.weekday() >= 5:
                continue
            for hhmm in ("10:00", "14:00"):
                if offset == 0 and hhmm in busy:
                    continue
                slots.append(f"{d.strftime('%A %b')} {d.day} at {hhmm}")
            if len(slots) >= 4:
                break
        return slots

    # ---- tools ----------------------------------------------------------------------------------------------------
    def tools(self) -> list[Tool]:
        return [
            Tool(
                "email_search",
                "Search the inbox. Returns recent messages (id, from, subject, snippet).",
                schema(
                    {
                        "query": {"type": "string", "description": "words to match; empty for the latest"},
                        "limit": {"type": "integer"},
                    }
                ),
                self._email_search,
                title="Search email",
            ),
            Tool(
                "email_read",
                "Read one email by id.",
                schema({"id": {"type": "string"}}, ["id"]),
                self._email_read,
                title="Read email",
            ),
            Tool(
                "email_send",
                "Send an email from the user's account.",
                schema(
                    {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                        "cc": {"type": "string"},
                    },
                    ["to", "subject", "body"],
                ),
                self._email_send,
                tags=frozenset({"send"}),
                editable=("to", "subject", "body", "cc"),
                summarize=lambda a: f"Send email to {a.get('to')}: {a.get('subject')}",
                title="Send email",
            ),
            Tool(
                "calendar_list",
                "List calendar events for the next N days, with free slots.",
                schema({"days": {"type": "integer"}}),
                self._calendar_list,
                title="Check calendar",
            ),
            Tool(
                "calendar_create",
                "Create a calendar event and invite attendees.",
                schema(
                    {
                        "title": {"type": "string"},
                        "start": {"type": "string"},
                        "duration_min": {"type": "integer"},
                        "attendees": {"type": "array", "items": {"type": "string"}},
                    },
                    ["title", "start"],
                ),
                self._calendar_create,
                tags=frozenset({"send", "write"}),
                editable=("title", "start", "duration_min"),
                summarize=lambda a: (
                    f"Invite {', '.join(a.get('attendees') or []) or 'no one'} to “{a.get('title')}” ({a.get('start')})"
                ),
                title="Create event",
            ),
            Tool(
                "payments_list",
                "List this week's payments and payouts (date, customer, amount, status).",
                schema(
                    {
                        "status": {
                            "type": "string",
                            "enum": ["paid", "overdue", "failed", "pending", "all"],
                            "description": "Only payments with this status; omit or 'all' for every payment.",
                        }
                    }
                ),
                self._payments_list,
                title="Read payments",
            ),
            Tool(
                "payments_pay",
                "Pay a bill or vendor.",
                schema(
                    {"payee": {"type": "string"}, "amount": {"type": "number"}, "memo": {"type": "string"}},
                    ["payee", "amount"],
                ),
                self._payments_pay,
                tags=frozenset({"pay"}),
                editable=("amount", "memo"),
                summarize=lambda a: f"Pay {a.get('payee')} ${float(a.get('amount') or 0):,.2f}",
                title="Make payment",
            ),
            Tool(
                "crm_lookup",
                "Look up a contact by name or company.",
                schema({"name": {"type": "string"}}, ["name"]),
                self._crm_lookup,
                title="Look up contact",
            ),
        ]

    async def _email_search(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        q = (args.get("query") or "").lower()
        msgs = [
            m
            for m in self._released_inbox()
            if not q or q in (m.get("subject", "") + m.get("body", "") + m.get("from", "")).lower()
        ]
        limit = int(args.get("limit") or 10)
        return {
            "messages": [
                {
                    "id": m["uid"],
                    "from": m["from"],
                    "subject": m["subject"],
                    "snippet": " ".join(m.get("body", "").split())[:140],
                    "date": m["when"].isoformat(),
                }
                for m in msgs[:limit]
            ]
        }

    async def _email_read(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        for m in self._released_inbox():
            if m["uid"] == args["id"] or m["id"] == args["id"]:
                return {"id": m["uid"], "from": m["from"], "subject": m["subject"], "body": m.get("body", "")}
        raise KeyError(f"no email {args['id']!r}")

    async def _email_send(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        rec = {
            "type": "email",
            "to": args["to"],
            "cc": args.get("cc"),
            "subject": args["subject"],
            "body": args["body"],
            "at": self.clock.now().isoformat(),
            "task_id": ctx.task_id,
        }
        self.outbox.append(rec)
        return {"sent": True, "to": args["to"], "subject": args["subject"]}

    async def _calendar_list(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        days = int(args.get("days") or 1)
        start = self._today()
        items = await self.upcoming(start, start + timedelta(days=days))
        return {
            "events": [
                {"title": c.title, "start": c.start.isoformat(), "location": c.location} for c in items
            ],
            "free_slots": self.free_slots(max(days, 3)),
        }

    async def _calendar_create(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        start = self._today() + timedelta(days=1, hours=10)
        item = CalendarItem(
            title=args["title"],
            start=start,
            end=start + timedelta(minutes=int(args.get("duration_min") or 30)),
            attendees=list(args.get("attendees") or []),
            source=self.name,
        )
        self.created_events.append(item)
        self.outbox.append(
            {
                "type": "invite",
                "title": args["title"],
                "start": args["start"],
                "attendees": item.attendees,
                "at": self.clock.now().isoformat(),
                "task_id": ctx.task_id,
            }
        )
        return {"created": True, "title": args["title"], "start": args["start"]}

    async def _payments_list(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        day = self._today()
        rows = []
        status = str(args.get("status") or "").strip().lower()
        if status in NO_FILTER:
            status = ""
        for p in self.data.get("payments", []):
            if status and p["status"] != status:
                continue
            rows.append(
                {
                    "id": p["id"],
                    "date": (day + timedelta(days=int(p["date"]))).date().isoformat(),
                    "customer": p["customer"],
                    "amount": p["amount"],
                    "status": p["status"],
                }
            )
        return {"payments": rows, "total": round(sum(r["amount"] for r in rows), 2)}

    async def _payments_pay(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        rec = {
            "type": "payment",
            "payee": args["payee"],
            "amount": float(args["amount"]),
            "memo": args.get("memo"),
            "at": self.clock.now().isoformat(),
            "task_id": ctx.task_id,
        }
        self.outbox.append(rec)
        return {
            "paid": True,
            "payee": args["payee"],
            "amount": float(args["amount"]),
            "confirmation": f"PAY-{len(self.outbox):04d}",
        }

    async def _crm_lookup(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        q = args["name"].lower()
        for c in self.data.get("contacts", []):
            if q in c["name"].lower() or q in c.get("company", "").lower() or q in c["email"]:
                return c
        return {"found": False, "name": args["name"]}

    def seed_memory(self) -> list[dict[str, Any]]:
        return list(self.data.get("memory", []))

    def user(self) -> dict[str, Any]:
        return dict(self.data.get("user") or {})
