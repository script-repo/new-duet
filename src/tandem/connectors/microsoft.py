"""Microsoft 365 (Outlook mail and calendar) over Microsoft Graph (SPEC §15.3). Setup: docs/CONNECTORS.md.

```yaml
- type: m365
  name: outlook              # tools: outlook_search, outlook_read, outlook_send, outlook_reply,
                             #        outlook_calendar_list, outlook_calendar_create
  tenant: common             # or your tenant id / domain
```
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from tandem.connectors.base import CalendarItem, InboundEvent, PushRequest, PushResult, register_connector
from tandem.connectors.oauth_base import ApiError, OAuthConnector
from tandem.connectors.simple import WebhookAuthError
from tandem.tools import Tool, ToolContext, schema
from tandem.util import iso, parse_iso

GRAPH = "https://graph.microsoft.com/v1.0"
MAIL_SELECT = (
    "id,subject,from,toRecipients,ccRecipients,receivedDateTime,bodyPreview,body,isRead,importance,"
    "conversationId,internetMessageId,inferenceClassification,categories"
)
PREFER = 'outlook.body-content-type="text", odata.maxpagesize=50, outlook.timezone="UTC"'
SUB_LIFETIME = timedelta(days=2, hours=12)  # under Graph's ~7-day cap for Outlook resources; renewed daily


def _addr(r: dict[str, Any] | None) -> str | None:
    return ((r or {}).get("emailAddress") or {}).get("address")


def _gdt(value: dict[str, Any] | None) -> datetime | None:
    """Graph dateTimeTimeZone; we ask for UTC with the Prefer header."""
    if not value or not value.get("dateTime"):
        return None
    dt = datetime.fromisoformat(value["dateTime"][:26])
    tz = value.get("timeZone") or "UTC"
    try:
        return dt.replace(tzinfo=UTC if tz == "UTC" else ZoneInfo(tz))
    except Exception:
        return dt.replace(tzinfo=UTC)


def parse_graph_message(m: dict[str, Any], source: str) -> InboundEvent:
    body = (m.get("body") or {}).get("content") or m.get("bodyPreview") or ""
    return InboundEvent(
        source=source,
        kind="email",
        external_id=m.get("internetMessageId") or m["id"],
        title=m.get("subject") or "(no subject)",
        body=body[:20000],
        sender=_addr(m.get("from")),
        occurred_at=parse_iso(m.get("receivedDateTime")),
        data={
            "id": m["id"],
            "thread_id": m.get("conversationId"),
            "importance": m.get("importance"),
            "inference_classification": m.get("inferenceClassification"),
            "labels": list(m.get("categories") or []),
            "is_read": m.get("isRead"),
            "to": [_addr(r) for r in m.get("toRecipients") or []],
            "headers": {},
        },
    )


@register_connector
class M365Connector(OAuthConnector):
    type = "m365"
    provider = "microsoft"
    display_name = "Microsoft 365"
    default_name = "outlook"
    scopes = [
        "openid",
        "email",
        "offline_access",
        "User.Read",
        "Mail.Read",
        "Mail.Send",
        "Calendars.ReadWrite",
    ]
    push_status = 202

    @property
    def send_tool(self) -> str | None:  # type: ignore[override]
        return f"{self.name}_send" if self.connected else None

    async def fetch_account(self) -> str | None:
        me = await self._api("GET", f"{GRAPH}/me", params={"$select": "mail,userPrincipalName"})
        return me.get("mail") or me.get("userPrincipalName")

    async def graph(self, method: str, path: str, **kw: Any) -> Any:
        url = path if path.startswith("https://") else f"{GRAPH}{path}"
        return await self._api(method, url, headers={"Prefer": PREFER, **kw.pop("headers", {})}, **kw)

    # ---- polling: delta queries ----
    async def _delta(
        self, key: str, first_url: str, first_params: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], bool]:
        """All changes since the stored deltaLink (or a fresh baseline); returns (items, was_first)."""
        link = self.kv_get(key)
        url, params, first = (link, None, False) if link else (first_url, first_params, True)
        items: list[dict[str, Any]] = []
        while True:
            try:
                res = await self.graph("GET", url, params=params)
            except ApiError as exc:
                if link and exc.status == 410:  # delta token expired: start over
                    self.kv_set(key, None)
                    return await self._delta(key, first_url, first_params)
                raise
            items += res.get("value") or []
            params = None
            if res.get("@odata.nextLink"):
                url = res["@odata.nextLink"]
                continue
            self.kv_set(key, res.get("@odata.deltaLink"))
            return items, first

    async def fetch_new(self, hint: dict[str, Any] | None = None) -> list[InboundEvent]:
        now = self.clock.now()
        mail, first = await self._delta(
            "mail_delta",
            f"{GRAPH}/me/mailFolders/inbox/messages/delta",
            {
                "$select": MAIL_SELECT,
                "$filter": f"receivedDateTime ge {(now - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            },
        )
        events = [
            parse_graph_message(m, self.name)
            for m in mail
            if "@removed" not in m and m.get("id") and (not first or not m.get("isRead"))
        ]
        cal, cal_first = await self._delta(
            "calendar_delta",
            f"{GRAPH}/me/calendarView/delta",
            {
                "startDateTime": iso(now),
                "endDateTime": iso(now + timedelta(days=int(self.options.get("calendar_days", 30)))),
            },
        )
        if not cal_first:
            events += [e for e in (self._calendar_event(c) for c in cal) if e]
        return events

    def _calendar_event(self, ev: dict[str, Any]) -> InboundEvent | None:
        if not ev.get("id"):
            return None
        title = ev.get("subject") or "(no title)"
        resp = (ev.get("responseStatus") or {}).get("response")
        if "@removed" in ev or ev.get("isCancelled"):
            change, label = "cancelled", f"Cancelled: {title if ev.get('subject') else 'an event'}"
        elif not ev.get("isOrganizer") and resp in ("none", "notResponded"):
            change, label = "invitation", f"Invitation: {title}"
        else:
            change, label = "updated", f"Changed: {title}"
        start = _gdt(ev.get("start"))
        organizer = _addr(ev.get("organizer"))
        return InboundEvent(
            source=self.name,
            kind="calendar",
            external_id=f"{ev['id']}:{ev.get('lastModifiedDateTime') or ev.get('changeKey') or change}",
            title=label,
            body=(ev.get("bodyPreview") or "")[:4000],
            sender=organizer,
            occurred_at=parse_iso(ev.get("lastModifiedDateTime")) or self.clock.now(),
            data={
                "event_id": ev["id"],
                "change": change,
                "start": iso(start),
                "end": iso(_gdt(ev.get("end"))),
                "location": (ev.get("location") or {}).get("displayName"),
                "organizer": organizer,
                "attendees": [
                    {"email": _addr(a), "response": (a.get("status") or {}).get("response")}
                    for a in ev.get("attendees") or []
                ],
                "response_status": resp,
                "link": ev.get("webLink"),
            },
        )

    async def upcoming(self, start: datetime, end: datetime) -> list[CalendarItem]:
        if not self.connected:
            return []
        res = await self.graph(
            "GET",
            "/me/calendarView",
            params={
                "startDateTime": iso(start),
                "endDateTime": iso(end),
                "$orderby": "start/dateTime",
                "$top": 100,
                "$select": "subject,start,end,location,attendees,isCancelled",
            },
        )
        out = []
        for ev in res.get("value") or []:
            s = _gdt(ev.get("start"))
            if s is None or ev.get("isCancelled"):
                continue
            out.append(
                CalendarItem(
                    title=ev.get("subject") or "(no title)",
                    start=s,
                    end=_gdt(ev.get("end")),
                    location=(ev.get("location") or {}).get("displayName") or None,
                    attendees=[_addr(a) or "" for a in ev.get("attendees") or []],
                    source=self.name,
                )
            )
        return out

    # ---- push: Graph change notifications ----
    def push_detail(self) -> str:
        exp = self.kv_get("push_expires_at")
        return f"Graph subscriptions until {exp}" if exp else "Graph subscriptions not created yet"

    async def subscribe(self, renew: bool) -> datetime | None:
        expires = self.clock.now() + SUB_LIFETIME
        subs: dict[str, str] = dict(self.kv_get("subscriptions") or {})
        wanted = {
            "mail": ("created", "me/mailFolders('inbox')/messages"),
            "calendar": ("created,updated,deleted", "me/events"),
        }
        for kind, (change, resource) in wanted.items():
            if renew and subs.get(kind):
                try:
                    await self.graph(
                        "PATCH", f"/subscriptions/{subs[kind]}", json={"expirationDateTime": iso(expires)}
                    )
                    continue
                except ApiError as exc:
                    if exc.status != 404:
                        raise
            res = await self.graph(
                "POST",
                "/subscriptions",
                json={
                    "changeType": change,
                    "notificationUrl": self.webhook_url(),
                    "resource": resource,
                    "expirationDateTime": iso(expires),
                    "clientState": self.push_secret(),
                },
            )
            subs[kind] = res["id"]
        self.kv_set("subscriptions", subs)
        return expires

    async def unsubscribe(self) -> None:
        for sid in (self.kv_get("subscriptions") or {}).values():
            try:
                await self.graph("DELETE", f"/subscriptions/{sid}")
            except ApiError as exc:
                if exc.status != 404:
                    raise
        self.kv_set("subscriptions", None)

    async def verify_push(self, req: PushRequest) -> PushResult | None:
        token = req.query.get("validationToken")
        if token is not None:  # subscription handshake: echo within 10 s as text/plain
            return PushResult(status=200, body=token, content_type="text/plain")
        notes = req.payload.get("value")
        if not isinstance(notes, list) or not notes:
            raise WebhookAuthError("no notifications")
        for n in notes:
            self.check_secret(str((n or {}).get("clientState") or ""))
        return None

    # ---- tools ----
    def tools(self) -> list[Tool]:
        n = self.name
        email_props = {
            "to": {"type": "string", "description": "comma-separated addresses"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "cc": {"type": "string"},
        }
        return [
            self.tool(
                f"{n}_search",
                "Search Outlook mail (Microsoft Search syntax, e.g. 'from:sam invoice'). Returns id, from, subject, date, snippet.",
                schema({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
                self._search,
                title="Search Outlook",
            ),
            self.tool(
                f"{n}_read",
                "Read one Outlook message by id.",
                schema({"id": {"type": "string"}}, ["id"]),
                self._read,
                title="Read email",
            ),
            self.tool(
                f"{n}_send",
                "Send a new email from the user's Outlook.",
                schema(email_props, ["to", "subject", "body"]),
                self._send,
                tags=frozenset({"send"}),
                editable=("to", "subject", "body", "cc"),
                summarize=lambda a: f"Send email to {a.get('to')}: {a.get('subject')}",
                title="Send email",
            ),
            self.tool(
                f"{n}_reply",
                "Reply to an Outlook message (id from search/read); set reply_all to include everyone.",
                schema(
                    {
                        "id": {"type": "string"},
                        "body": {"type": "string"},
                        "reply_all": {"type": "boolean"},
                        "subject": {"type": "string", "description": "for the card only"},
                    },
                    ["id", "body"],
                ),
                self._reply,
                tags=frozenset({"send"}),
                editable=("body",),
                summarize=lambda a: (
                    f"Reply{' all' if a.get('reply_all') else ''} "
                    f"{('re: ' + a['subject']) if a.get('subject') else '(message ' + str(a.get('id'))[:12] + ')'}"
                ),
                title="Reply to email",
            ),
            self.tool(
                f"{n}_calendar_list",
                "List Outlook calendar events for the next N days.",
                schema({"days": {"type": "integer"}}),
                self._calendar_list,
                title="Check calendar",
            ),
            self.tool(
                f"{n}_calendar_create",
                "Create an Outlook calendar event; attendees get an invitation.",
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
                self._calendar_create,
                tags=frozenset({"send", "write"}),
                editable=("title", "start", "duration_min", "attendees", "location", "description"),
                summarize=lambda a: (
                    f"Create “{a.get('title')}” at {a.get('start')}"
                    + (f" with {', '.join(a['attendees'])}" if a.get("attendees") else "")
                ),
                title="Create event",
            ),
        ]

    async def _search(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        q = str(args["query"]).replace('"', "")
        res = await self.graph(
            "GET",
            "/me/messages",
            params={
                "$search": f'"{q}"',
                "$top": min(int(args.get("limit") or 10), 50),
                "$select": "id,subject,from,receivedDateTime,bodyPreview,conversationId",
            },
        )
        return {
            "messages": [
                {
                    "id": m["id"],
                    "thread_id": m.get("conversationId"),
                    "from": _addr(m.get("from")),
                    "subject": m.get("subject"),
                    "date": m.get("receivedDateTime"),
                    "snippet": m.get("bodyPreview", "")[:200],
                }
                for m in res.get("value") or []
            ]
        }

    async def _read(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        m = await self.graph("GET", f"/me/messages/{args['id']}", params={"$select": MAIL_SELECT})
        return {
            "id": m["id"],
            "thread_id": m.get("conversationId"),
            "from": _addr(m.get("from")),
            "to": [_addr(r) for r in m.get("toRecipients") or []],
            "cc": [_addr(r) for r in m.get("ccRecipients") or []],
            "subject": m.get("subject"),
            "date": m.get("receivedDateTime"),
            "body": (m.get("body") or {}).get("content") or m.get("bodyPreview", ""),
        }

    @staticmethod
    def _recipients(value: str | None) -> list[dict[str, Any]]:
        return [{"emailAddress": {"address": a.strip()}} for a in (value or "").split(",") if a.strip()]

    async def _send(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        message: dict[str, Any] = {
            "subject": args["subject"],
            "body": {"contentType": "Text", "content": args["body"]},
            "toRecipients": self._recipients(args["to"]),
        }
        if args.get("cc"):
            message["ccRecipients"] = self._recipients(args["cc"])
        await self.graph("POST", "/me/sendMail", json={"message": message, "saveToSentItems": True})
        return {"sent": True, "to": args["to"], "subject": args["subject"]}

    async def _reply(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        action = "replyAll" if args.get("reply_all") else "reply"
        await self.graph("POST", f"/me/messages/{args['id']}/{action}", json={"comment": args["body"]})
        return {"sent": True, "id": args["id"], "reply_all": bool(args.get("reply_all"))}

    async def _calendar_list(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        now = self.clock.now()
        items = await self.upcoming(now, now + timedelta(days=int(args.get("days") or 1)))
        return {"events": [i.public() for i in items]}

    async def _calendar_create(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        start = datetime.fromisoformat(args["start"])
        if start.tzinfo is None:
            start = start.replace(tzinfo=ZoneInfo(self.options.get("timezone", "UTC")))
        end = start + timedelta(minutes=int(args.get("duration_min") or 30))
        fmt = "%Y-%m-%dT%H:%M:%S"
        body: dict[str, Any] = {
            "subject": args["title"],
            "start": {"dateTime": start.astimezone(UTC).strftime(fmt), "timeZone": "UTC"},
            "end": {"dateTime": end.astimezone(UTC).strftime(fmt), "timeZone": "UTC"},
        }
        if args.get("attendees"):
            body["attendees"] = [
                {"emailAddress": {"address": a}, "type": "required"} for a in args["attendees"]
            ]
        if args.get("location"):
            body["location"] = {"displayName": args["location"]}
        if args.get("description"):
            body["body"] = {"contentType": "Text", "content": args["description"]}
        res = await self.graph("POST", "/me/events", json=body)
        return {
            "created": True,
            "id": res.get("id"),
            "title": args["title"],
            "start": start.isoformat(),
            "link": res.get("webLink"),
        }
