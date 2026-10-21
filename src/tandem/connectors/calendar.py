"""Calendars: any ICS feed or file (read-only, built-in parser), or CalDAV via the optional `caldav` package.

```yaml
- type: ics
  name: calendar             # tool: calendar_list
  url: https://calendar.example.com/me.ics     # or path: ./me.ics
- type: caldav
  name: calendar             # tools: calendar_list, calendar_create
  url: https://caldav.example.com/
  username: me
  password_env: CALDAV_PASSWORD
```
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from tandem.connectors.base import CalendarItem, Connector, InboundEvent, register_connector
from tandem.tools import Tool, ToolContext, schema


def unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(v: str) -> str:
    return (
        v.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def parse_dt(value: str, params: dict[str, str], default_tz: ZoneInfo) -> tuple[datetime, bool]:
    """Returns (datetime, all_day)."""
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        d = date(int(value[:4]), int(value[4:6]), int(value[6:8]))
        return datetime(d.year, d.month, d.day, tzinfo=default_tz), True
    fmt = "%Y%m%dT%H%M%S"
    if value.endswith("Z"):
        return datetime.strptime(value[:-1], fmt).replace(tzinfo=UTC), False
    tz = default_tz
    if "TZID" in params:
        try:
            tz = ZoneInfo(params["TZID"])
        except Exception:
            tz = default_tz
    return datetime.strptime(value[:15], fmt).replace(tzinfo=tz), False


def parse_ics(text: str, default_tz: str = "UTC", source: str = "") -> list[CalendarItem]:
    tz = ZoneInfo(default_tz)
    items: list[CalendarItem] = []
    cur: dict[str, Any] | None = None
    for line in unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {"attendees": []}
        elif line == "END:VEVENT" and cur is not None:
            if "start" in cur:
                items.append(
                    CalendarItem(
                        title=cur.get("summary", "(no title)"),
                        start=cur["start"],
                        end=cur.get("end"),
                        location=cur.get("location"),
                        attendees=cur["attendees"],
                        source=source,
                    )
                )
            cur = None
        elif cur is not None and ":" in line:
            head, value = line.split(":", 1)
            name, *raw_params = head.split(";")
            params = dict(p.split("=", 1) for p in raw_params if "=" in p)
            name = name.upper()
            if name == "SUMMARY":
                cur["summary"] = _unescape(value)
            elif name == "LOCATION":
                cur["location"] = _unescape(value)
            elif name == "UID":
                cur["uid"] = value
            elif name in ("DTSTART", "DTEND"):
                dt, all_day = parse_dt(value, params, tz)
                cur["start" if name == "DTSTART" else "end"] = dt
                if all_day and name == "DTSTART":
                    cur.setdefault("end", dt + timedelta(days=1))
            elif name == "ATTENDEE":
                cur["attendees"].append(value.replace("mailto:", "").replace("MAILTO:", ""))
    return sorted(items, key=lambda i: i.start)


@register_connector
class IcsConnector(Connector):
    type = "ics"

    async def _load(self) -> list[CalendarItem]:
        tz = self.options.get("timezone", "UTC")
        if self.options.get("path"):
            text = Path(self.options["path"]).read_text(encoding="utf-8")
        else:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                r = await client.get(self.options["url"])
                r.raise_for_status()
                text = r.text
        return parse_ics(text, tz, self.name)

    async def upcoming(self, start: datetime, end: datetime) -> list[CalendarItem]:
        try:
            items = await self._load()
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)
            return []
        return [i for i in items if start <= i.start <= end or (i.end and i.start <= start < i.end)]

    async def poll(self, since: datetime | None) -> list[InboundEvent]:
        """Events starting within the lookahead (default 2h) become inbound items for triage."""
        self.last_poll = now = self.clock.now()
        ahead = timedelta(minutes=int(self.options.get("lookahead_min", 120)))
        out = []
        for i in await self.upcoming(now, now + ahead):
            out.append(
                InboundEvent(
                    source=self.name,
                    kind="calendar",
                    external_id=f"{i.title}-{i.start.isoformat()}",
                    title=f"Starting soon: {i.title}",
                    body=f"{i.start.isoformat()} {i.location or ''}",
                    occurred_at=now,
                    data=i.public(),
                )
            )
        return out

    def tools(self) -> list[Tool]:
        return [
            Tool(
                f"{self.name}_list",
                "List calendar events for the next N days.",
                schema({"days": {"type": "integer"}}),
                self._list,
                title="Check calendar",
            )
        ]

    async def _list(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        now = self.clock.now()
        items = await self.upcoming(now, now + timedelta(days=int(args.get("days") or 1)))
        return {"events": [i.public() for i in items]}


@register_connector
class CalDavConnector(IcsConnector):
    type = "caldav"

    def _client(self) -> Any:
        try:
            import caldav
        except ImportError as exc:
            raise RuntimeError("install the caldav extra: uv sync --extra caldav") from exc
        pw = self.secrets.get(self.options.get("password_env")) if self.secrets else None
        return caldav.DAVClient(url=self.options["url"], username=self.options.get("username"), password=pw)

    def _calendar(self) -> Any:
        principal = self._client().principal()
        cals = principal.calendars()
        want = self.options.get("calendar")
        for c in cals:
            if not want or c.name == want:
                return c
        raise RuntimeError("no calendar found")

    async def _load(self) -> list[CalendarItem]:
        def fetch() -> str:
            now = datetime.now(UTC)
            cal = self._calendar()
            results = cal.search(
                start=now - timedelta(days=1), end=now + timedelta(days=14), event=True, expand=True
            )
            return "\n".join(r.data for r in results)

        return parse_ics(await asyncio.to_thread(fetch), self.options.get("timezone", "UTC"), self.name)

    def tools(self) -> list[Tool]:
        return [
            *super().tools(),
            Tool(
                f"{self.name}_create",
                "Create a calendar event (and invite attendees).",
                schema(
                    {
                        "title": {"type": "string"},
                        "start": {"type": "string", "description": "ISO 8601"},
                        "duration_min": {"type": "integer"},
                        "attendees": {"type": "array", "items": {"type": "string"}},
                    },
                    ["title", "start"],
                ),
                self._create,
                tags=frozenset({"send", "write"}),
                editable=("title", "start", "duration_min"),
                summarize=lambda a: f"Create “{a.get('title')}” at {a.get('start')}",
                title="Create event",
            ),
        ]

    async def _create(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        start = datetime.fromisoformat(args["start"])
        end = start + timedelta(minutes=int(args.get("duration_min") or 30))

        def make() -> None:
            self._calendar().save_event(dtstart=start, dtend=end, summary=args["title"])

        await asyncio.to_thread(make)
        return {"created": True, "title": args["title"], "start": start.isoformat()}
