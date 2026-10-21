"""Gmail and Google Calendar over OAuth (SPEC §15.3). Setup: docs/CONNECTORS.md.

```yaml
- type: gmail
  name: gmail                # tools: gmail_search, gmail_read, gmail_send, gmail_reply
  pubsub_topic: projects/my-project/topics/tandem-gmail   # optional: push via Pub/Sub
- type: google_calendar
  name: calendar             # tools: calendar_list, calendar_create
  calendar_id: primary
```
"""

from __future__ import annotations

import base64
import re
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html import unescape
from typing import Any

from tandem.connectors.base import CalendarItem, InboundEvent, PushRequest, PushResult, register_connector
from tandem.connectors.oauth_base import ApiError, OAuthConnector, _fmt
from tandem.connectors.simple import WebhookAuthError
from tandem.tools import Tool, ToolContext, schema
from tandem.util import iso, parse_iso

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CAL = "https://www.googleapis.com/calendar/v3"
TRIAGE_HEADERS = ("list-unsubscribe", "list-id", "precedence", "auto-submitted")


def _b64decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
    return unescape(re.sub(r"<[^>]+>", " ", text)).strip()


def _gmail_body(payload: dict[str, Any]) -> str:
    """The text/plain part (else stripped text/html) of a Gmail `format=full` payload."""
    plain: list[str] = []
    html: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data and not part.get("filename"):
            if mime == "text/plain":
                plain.append(_b64decode(data))
            elif mime == "text/html":
                html.append(_b64decode(data))
        for sub in part.get("parts") or []:
            walk(sub)

    walk(payload)
    if plain:
        return "\n".join(plain)
    return _strip_html("\n".join(html)) if html else ""


def _headers(msg: dict[str, Any]) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in (msg.get("payload") or {}).get("headers") or []}


def parse_gmail(msg: dict[str, Any], source: str) -> InboundEvent:
    """Normalize a Gmail message resource, keeping triage's signals (headers, labels, thread)."""
    h = _headers(msg)
    when: datetime | None = None
    if msg.get("internalDate"):
        when = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, UTC)
    elif h.get("date"):
        try:
            when = parsedate_to_datetime(h["date"])
        except (TypeError, ValueError):
            when = None
    labels = list(msg.get("labelIds") or [])
    body = _gmail_body(msg.get("payload") or {}) or msg.get("snippet", "")
    return InboundEvent(
        source=source,
        kind="email",
        external_id=h.get("message-id") or msg["id"],
        title=h.get("subject") or "(no subject)",
        body=body[:20000],
        sender=parseaddr(h.get("from", ""))[1] or None,
        occurred_at=when,
        data={
            "id": msg["id"],
            "thread_id": msg.get("threadId"),
            "labels": labels,
            "headers": {k: v for k, v in h.items() if k in TRIAGE_HEADERS},
            "important": "IMPORTANT" in labels,
            "to": h.get("to"),
            "cc": h.get("cc"),
        },
    )


def build_mime(
    sender: str | None,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> str:
    """A base64url RFC 2822 message for Gmail's `messages.send`."""
    msg = EmailMessage()
    if sender:
        msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = f"{references} {in_reply_to}".strip() if references else in_reply_to
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def _ms(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value) / 1000, UTC) if value else None


# ---- Gmail ------------------------------------------------------------------------------------------------


@register_connector
class GmailConnector(OAuthConnector):
    type = "gmail"
    provider = "google"
    display_name = "Gmail"
    default_name = "gmail"
    scopes = [
        "openid",
        "email",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ]

    @property
    def send_tool(self) -> str | None:  # type: ignore[override]
        return f"{self.name}_send" if self.connected else None

    def push_unavailable(self) -> str | None:
        if not self.options.get("pubsub_topic"):
            return f"polling every {_fmt(self.poll_every)}: set pubsub_topic for push"
        return None

    def push_detail(self) -> str:
        exp = self.kv_get("push_expires_at")
        url = f"{self.webhook_url()}?token=<push token>"
        return f"Pub/Sub push to {url}; watch until {exp}" if exp else f"Pub/Sub push to {url}"

    def push_endpoint(self) -> str:
        """The URL to give the Pub/Sub push subscription (carries the push token)."""
        return f"{self.webhook_url()}?token={self.push_secret()}"

    async def fetch_account(self) -> str | None:
        prof = await self._api("GET", f"{GMAIL}/profile")
        return prof.get("emailAddress")

    # ---- polling ----
    async def _get(self, msg_id: str, fmt: str = "full") -> dict[str, Any]:
        return await self._api("GET", f"{GMAIL}/messages/{msg_id}", params={"format": fmt})

    async def _baseline(self) -> list[InboundEvent]:
        """First run (or after an expired historyId): unread inbox mail from the last day."""
        prof = await self._api("GET", f"{GMAIL}/profile")
        listing = await self._api(
            "GET",
            f"{GMAIL}/messages",
            params={
                "q": self.options.get("first_query", "in:inbox is:unread newer_than:1d"),
                "maxResults": int(self.options.get("max_per_poll", 25)),
            },
        )
        ids = [m["id"] for m in listing.get("messages") or []]
        events = [parse_gmail(await self._get(i), self.name) for i in ids]
        self.kv_set("history_id", str(prof.get("historyId")))
        return events

    async def fetch_new(self, hint: dict[str, Any] | None = None) -> list[InboundEvent]:
        start = self.kv_get("history_id")
        if not start:
            return await self._baseline()
        added: list[str] = []
        page: str | None = None
        latest = start
        while True:
            params: dict[str, Any] = {
                "startHistoryId": start,
                "historyTypes": "messageAdded",
                "labelId": "INBOX",
            }
            if page:
                params["pageToken"] = page
            try:
                res = await self._api("GET", f"{GMAIL}/history", params=params)
            except ApiError as exc:
                if exc.status == 404:  # historyId too old: start over
                    self.kv_set("history_id", None)
                    return await self._baseline()
                raise
            for h in res.get("history") or []:
                for m in h.get("messagesAdded") or []:
                    msg = m.get("message") or {}
                    labels = msg.get("labelIds") or []
                    if msg.get("id") and "INBOX" in labels and not {"SENT", "DRAFT"} & set(labels):
                        added.append(msg["id"])
            latest = str(res.get("historyId") or latest)
            page = res.get("nextPageToken")
            if not page:
                break
        events = []
        for mid in dict.fromkeys(added):
            try:
                events.append(parse_gmail(await self._get(mid), self.name))
            except ApiError as exc:
                if exc.status != 404:  # deleted since
                    raise
        self.kv_set("history_id", latest)
        return events

    # ---- push ----
    async def subscribe(self, renew: bool) -> datetime | None:
        self.push_secret()
        res = await self._api(
            "POST",
            f"{GMAIL}/watch",
            json={
                "topicName": self.options["pubsub_topic"],
                "labelIds": ["INBOX"],
                "labelFilterBehavior": "include",
            },
        )
        if not self.kv_get("history_id") and res.get("historyId"):
            self.kv_set("history_id", str(res["historyId"]))
        # Google asks to re-call watch at least every 7 days; renew daily.
        exp = _ms(res.get("expiration"))
        daily = self.clock.now() + timedelta(days=2)
        return min(exp, daily) if exp else daily

    async def unsubscribe(self) -> None:
        if self.kv_get("push_expires_at"):
            await self._api("POST", f"{GMAIL}/stop")

    async def verify_push(self, req: PushRequest) -> PushResult | None:
        self.check_secret(req.query.get("token"))
        return None

    # ---- tools ----
    def tools(self) -> list[Tool]:
        n = self.name
        return [
            self.tool(
                f"{n}_search",
                "Search Gmail (Gmail search syntax, e.g. 'from:sam newer_than:7d'). Returns id, thread_id, from, subject, date, snippet.",
                schema({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
                self._search,
                title="Search Gmail",
            ),
            self.tool(
                f"{n}_read",
                "Read one Gmail message by id.",
                schema({"id": {"type": "string"}}, ["id"]),
                self._read,
                title="Read email",
            ),
            self.tool(
                f"{n}_send",
                "Send a new email from the user's Gmail.",
                schema(
                    {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                        "cc": {"type": "string"},
                    },
                    ["to", "subject", "body"],
                ),
                self._send,
                tags=frozenset({"send"}),
                editable=("to", "subject", "body", "cc"),
                summarize=lambda a: f"Send email to {a.get('to')}: {a.get('subject')}",
                title="Send email",
            ),
            self.tool(
                f"{n}_reply",
                "Reply in the thread of a Gmail message (id from search/read). Replies to the sender unless 'to' is given.",
                schema(
                    {
                        "id": {"type": "string"},
                        "body": {"type": "string"},
                        "to": {"type": "string"},
                        "cc": {"type": "string"},
                        "subject": {
                            "type": "string",
                            "description": "for the card; defaults to Re: <subject>",
                        },
                    },
                    ["id", "body"],
                ),
                self._reply,
                tags=frozenset({"send"}),
                editable=("body", "to", "cc"),
                summarize=lambda a: (
                    f"Reply {('to ' + a['to'] + ' ') if a.get('to') else ''}"
                    f"{('re: ' + a['subject']) if a.get('subject') else '(message ' + str(a.get('id')) + ')'}"
                ),
                title="Reply to email",
            ),
        ]

    async def _search(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        listing = await self._api(
            "GET",
            f"{GMAIL}/messages",
            params={"q": args.get("query") or "", "maxResults": min(int(args.get("limit") or 10), 50)},
        )
        out = []
        for m in listing.get("messages") or []:
            msg = await self._api(
                "GET",
                f"{GMAIL}/messages/{m['id']}",
                params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
            )
            h = _headers(msg)
            out.append(
                {
                    "id": msg["id"],
                    "thread_id": msg.get("threadId"),
                    "from": h.get("from"),
                    "subject": h.get("subject"),
                    "date": h.get("date"),
                    "snippet": unescape(msg.get("snippet", "")),
                }
            )
        return {"messages": out}

    async def _read(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        msg = await self._get(args["id"])
        h = _headers(msg)
        return {
            "id": msg["id"],
            "thread_id": msg.get("threadId"),
            "from": h.get("from"),
            "to": h.get("to"),
            "cc": h.get("cc"),
            "subject": h.get("subject"),
            "date": h.get("date"),
            "labels": msg.get("labelIds") or [],
            "body": _gmail_body(msg.get("payload") or {}) or msg.get("snippet", ""),
        }

    async def _send(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        raw = build_mime(self.account(), args["to"], args["subject"], args["body"], args.get("cc"))
        res = await self._api("POST", f"{GMAIL}/messages/send", json={"raw": raw})
        return {"sent": True, "id": res.get("id"), "to": args["to"], "subject": args["subject"]}

    async def _reply(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        orig = await self._api(
            "GET",
            f"{GMAIL}/messages/{args['id']}",
            params={
                "format": "metadata",
                "metadataHeaders": ["From", "Reply-To", "Subject", "Message-ID", "References"],
            },
        )
        h = _headers(orig)
        to = args.get("to") or h.get("reply-to") or h.get("from") or ""
        if not getaddresses([to]) or not to:
            raise ValueError("couldn't tell who to reply to; pass 'to'")
        subject = h.get("subject") or ""
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}".strip()
        raw = build_mime(
            self.account(),
            to,
            subject,
            args["body"],
            args.get("cc"),
            in_reply_to=h.get("message-id"),
            references=h.get("references"),
        )
        res = await self._api(
            "POST", f"{GMAIL}/messages/send", json={"raw": raw, "threadId": orig.get("threadId")}
        )
        return {
            "sent": True,
            "id": res.get("id"),
            "thread_id": res.get("threadId"),
            "to": to,
            "subject": subject,
        }


# ---- Google Calendar --------------------------------------------------------------------------------------


def _gdt(value: dict[str, Any] | None, tz: str = "UTC") -> datetime | None:
    if not value:
        return None
    if value.get("dateTime"):
        return parse_iso(value["dateTime"])
    if value.get("date"):
        return datetime.fromisoformat(value["date"]).replace(tzinfo=UTC)
    return None


@register_connector
class GoogleCalendarConnector(OAuthConnector):
    type = "google_calendar"
    provider = "google"
    display_name = "Google Calendar"
    default_name = "calendar"
    scopes = ["openid", "email", "https://www.googleapis.com/auth/calendar.events"]

    @property
    def calendar_id(self) -> str:
        return str(self.options.get("calendar_id") or "primary")

    def _events_url(self) -> str:
        return f"{CAL}/calendars/{self.calendar_id}/events"

    async def fetch_account(self) -> str | None:
        cal = await self._api("GET", f"{CAL}/calendars/{self.calendar_id}")
        return cal.get("id")

    # ---- polling ----
    def _normalize(self, ev: dict[str, Any]) -> InboundEvent | None:
        start = _gdt(ev.get("start"))
        me = next((a for a in ev.get("attendees") or [] if a.get("self")), None)
        organizer = (ev.get("organizer") or {}).get("email")
        title = ev.get("summary") or "(no title)"
        if ev.get("status") == "cancelled":
            change = "cancelled"
            label = f"Cancelled: {title}"
        elif me and me.get("responseStatus") == "needsAction" and not (ev.get("organizer") or {}).get("self"):
            change = "invitation"
            label = f"Invitation: {title}"
        else:
            change = "updated"
            label = f"Changed: {title}"
        if start and start < self.clock.now() - timedelta(hours=1) and change != "cancelled":
            return None  # changes to past events don't need anyone
        return InboundEvent(
            source=self.name,
            kind="calendar",
            external_id=f"{ev['id']}:{ev.get('updated', '')}",
            title=label,
            body=(ev.get("description") or "")[:4000],
            sender=organizer,
            occurred_at=parse_iso(ev.get("updated")) or self.clock.now(),
            data={
                "event_id": ev["id"],
                "change": change,
                "start": iso(start),
                "end": iso(_gdt(ev.get("end"))),
                "location": ev.get("location"),
                "organizer": organizer,
                "attendees": [
                    {"email": a.get("email"), "response": a.get("responseStatus")}
                    for a in ev.get("attendees") or []
                ],
                "response_status": (me or {}).get("responseStatus"),
                "link": ev.get("htmlLink"),
            },
        )

    async def _list_all(self, params: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
        items: list[dict[str, Any]] = []
        page: str | None = None
        while True:
            p = dict(params)
            if page:
                p["pageToken"] = page
            res = await self._api("GET", self._events_url(), params=p)
            items += res.get("items") or []
            page = res.get("nextPageToken")
            if not page:
                return items, res.get("nextSyncToken")

    async def fetch_new(self, hint: dict[str, Any] | None = None) -> list[InboundEvent]:
        token = self.kv_get("sync_token")
        synced_at = parse_iso(self.kv_get("synced_at"))
        now = self.clock.now()
        if token:
            try:
                items, nxt = await self._list_all(
                    {"syncToken": token, "showDeleted": "true", "singleEvents": "true", "maxResults": 250}
                )
            except ApiError as exc:
                if exc.status != 410:
                    raise
                token = None  # sync token expired: full resync below
        if not token:
            items, nxt = await self._list_all(
                {"showDeleted": "true", "singleEvents": "true", "maxResults": 250}
            )
            if synced_at is None:  # first run: a baseline, not a flood of old events
                items = []
            else:  # resync: only what changed since we last looked
                items = [i for i in items if (parse_iso(i.get("updated")) or now) > synced_at]
        self.kv_set("sync_token", nxt)
        self.kv_set("synced_at", iso(now))
        return [e for e in (self._normalize(i) for i in items) if e]

    async def upcoming(self, start: datetime, end: datetime) -> list[CalendarItem]:
        if not self.connected:
            return []
        items, _ = await self._list_all(
            {
                "timeMin": iso(start),
                "timeMax": iso(end),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 100,
            }
        )
        out = []
        for ev in items:
            s = _gdt(ev.get("start"))
            if s is None or ev.get("status") == "cancelled":
                continue
            out.append(
                CalendarItem(
                    title=ev.get("summary") or "(no title)",
                    start=s,
                    end=_gdt(ev.get("end")),
                    location=ev.get("location"),
                    attendees=[a.get("email", "") for a in ev.get("attendees") or []],
                    source=self.name,
                )
            )
        return out

    # ---- push ----
    async def subscribe(self, renew: bool) -> datetime | None:
        if renew:
            await self.unsubscribe()
        channel = uuid.uuid4().hex
        res = await self._api(
            "POST",
            f"{self._events_url()}/watch",
            json={
                "id": channel,
                "type": "web_hook",
                "address": self.webhook_url(),
                "token": self.push_secret(),
                "params": {"ttl": str(7 * 86400)},
            },
        )
        self.kv_set("channel", {"id": channel, "resource_id": res.get("resourceId")})
        return _ms(res.get("expiration")) or self.clock.now() + timedelta(days=7)

    async def unsubscribe(self) -> None:
        ch = self.kv_get("channel")
        if ch:
            await self._api(
                "POST", f"{CAL}/channels/stop", json={"id": ch["id"], "resourceId": ch["resource_id"]}
            )
            self.kv_set("channel", None)

    async def verify_push(self, req: PushRequest) -> PushResult | None:
        self.check_secret(req.headers.get("x-goog-channel-token"))
        ch = self.kv_get("channel") or {}
        if req.headers.get("x-goog-channel-id") != ch.get("id"):
            raise WebhookAuthError("unknown channel")
        if req.headers.get("x-goog-resource-state") == "sync":
            return PushResult(body={"accepted": 0})
        return None

    # ---- tools ----
    def tools(self) -> list[Tool]:
        n = self.name
        return [
            self.tool(
                f"{n}_list",
                "List Google Calendar events for the next N days.",
                schema({"days": {"type": "integer"}}),
                self._list,
                title="Check calendar",
            ),
            self.tool(
                f"{n}_create",
                "Create a Google Calendar event; attendees get an email invitation.",
                schema(
                    {
                        "title": {"type": "string"},
                        "start": {"type": "string", "description": "ISO 8601 with offset"},
                        "duration_min": {"type": "integer"},
                        "attendees": {"type": "array", "items": {"type": "string"}},
                        "location": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    ["title", "start"],
                ),
                self._create,
                tags=frozenset({"send", "write"}),
                editable=("title", "start", "duration_min", "attendees", "location", "description"),
                summarize=lambda a: (
                    f"Create “{a.get('title')}” at {a.get('start')}"
                    + (f" with {', '.join(a['attendees'])}" if a.get("attendees") else "")
                ),
                title="Create event",
            ),
        ]

    async def _list(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        now = self.clock.now()
        items = await self.upcoming(now, now + timedelta(days=int(args.get("days") or 1)))
        return {"events": [i.public() for i in items]}

    async def _create(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        start = datetime.fromisoformat(args["start"])
        if start.tzinfo is None:
            from zoneinfo import ZoneInfo

            start = start.replace(tzinfo=ZoneInfo(self.options.get("timezone", "UTC")))
        end = start + timedelta(minutes=int(args.get("duration_min") or 30))
        body: dict[str, Any] = {
            "summary": args["title"],
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
        if args.get("attendees"):
            body["attendees"] = [{"email": a} for a in args["attendees"]]
        for k in ("location", "description"):
            if args.get(k):
                body[k] = args[k]
        res = await self._api("POST", self._events_url(), params={"sendUpdates": "all"}, json=body)
        return {
            "created": True,
            "id": res.get("id"),
            "title": args["title"],
            "start": start.isoformat(),
            "link": res.get("htmlLink"),
        }
