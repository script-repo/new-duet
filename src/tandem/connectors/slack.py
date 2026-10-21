"""Slack over OAuth with a user token (SPEC §15.3): Tandem reads and posts as the user. Setup: docs/CONNECTORS.md.

```yaml
- type: slack
  name: slack                # tools: slack_search, slack_history, slack_post
  channels: [C0123456789, general]   # channels to watch besides DMs and group DMs
  signing_secret_env: SLACK_SIGNING_SECRET   # Events API push
```
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any

from tandem.connectors.base import InboundEvent, PushRequest, PushResult, register_connector
from tandem.connectors.oauth_base import ApiError, OAuthConnector
from tandem.connectors.simple import WebhookAuthError
from tandem.tools import Tool, ToolContext, schema

API = "https://slack.com/api"
REPLAY_WINDOW = timedelta(minutes=5)
KEEP_SUBTYPES = {None, "thread_broadcast", "file_share", "me_message"}


def slack_signature(secret: str, timestamp: str, raw: bytes) -> str:
    base = b"v0:" + timestamp.encode() + b":" + raw
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def _ts(value: str | None) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value), UTC) if value else None
    except ValueError:
        return None


def _channel_type(conv: dict[str, Any]) -> str:
    if conv.get("is_im"):
        return "im"
    if conv.get("is_mpim"):
        return "mpim"
    return "private_channel" if conv.get("is_private") else "channel"


@register_connector
class SlackConnector(OAuthConnector):
    type = "slack"
    provider = "slack"
    display_name = "Slack"
    default_name = "slack"
    scopes = [
        "channels:history",
        "groups:history",
        "im:history",
        "mpim:history",
        "channels:read",
        "groups:read",
        "im:read",
        "mpim:read",
        "users:read",
        "users:read.email",
        "search:read",
        "chat:write",
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._users: dict[str, dict[str, Any]] = {}
        self._convs: tuple[datetime, list[dict[str, Any]]] | None = None

    # ---- HTTP: Slack reports errors as 200 {"ok": false} ----
    def check(self, body: Any) -> Any:
        if isinstance(body, dict) and body.get("ok") is False:
            err = str(body.get("error") or "error")
            raise ApiError(
                f"Slack API error: {err}", 401 if err in ("token_expired", "invalid_auth") else 400, err
            )
        return body

    async def _api(self, method: str, url: str, **kw: Any) -> Any:  # type: ignore[override]
        try:
            return await super()._api(method, url, **kw)
        except ApiError as exc:
            if exc.code != "token_expired":
                raise
            await self.force_refresh()
            return await super()._api(method, url, **kw)

    async def call(self, api_method: str, **params: Any) -> dict[str, Any]:
        return await self._api("GET", f"{API}/{api_method}", params=params)

    def self_id(self) -> str | None:
        tok = self.vault.token(self.name) if self.vault else None
        return (tok or {}).get("user_id")

    async def fetch_account(self) -> str | None:
        res = await self.call("auth.test")
        user, team = res.get("user"), res.get("team")
        return f"{user} @ {team}" if user and team else team or user

    # ---- push: Events API ----
    def _signing_secret(self) -> str | None:
        env = self.options.get("signing_secret_env") or "SLACK_SIGNING_SECRET"
        return self.secrets.get(env) if self.secrets else None

    def push_unavailable(self) -> str | None:
        if not self._signing_secret():
            return "polling: set SLACK_SIGNING_SECRET (or signing_secret_env) for Events API push"
        return None

    def push_detail(self) -> str:
        return f"Events API request URL {self.webhook_url()}"

    async def verify_push(self, req: PushRequest) -> PushResult | None:
        secret = self._signing_secret()
        if not secret:
            raise WebhookAuthError("Slack signing secret is not set")
        ts = req.headers.get("x-slack-request-timestamp", "")
        when = _ts(ts)
        if when is None or abs(self.clock.now() - when) > REPLAY_WINDOW:
            raise WebhookAuthError("stale or missing X-Slack-Request-Timestamp")
        given = req.headers.get("x-slack-signature", "")
        if not hmac.compare_digest(given, slack_signature(secret, ts, req.raw)):
            raise WebhookAuthError("bad X-Slack-Signature")
        if req.payload.get("type") == "url_verification":
            return PushResult(body={"challenge": str(req.payload.get("challenge", ""))})
        return None

    def push_hint(self, req: PushRequest) -> dict[str, Any] | None:
        ev = req.payload.get("event") or {}
        return {"channel": ev["channel"]} if isinstance(ev, dict) and ev.get("channel") else None

    # ---- polling ----
    async def _conversations(self) -> list[dict[str, Any]]:
        now = self.clock.now()
        if self._convs and now - self._convs[0] < timedelta(hours=1):
            return self._convs[1]
        wanted = {str(c).lstrip("#") for c in self.options.get("channels") or []}
        convs: list[dict[str, Any]] = []
        cursor = None
        while True:
            params: dict[str, Any] = {
                "types": "im,mpim,public_channel,private_channel",
                "exclude_archived": "true",
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor
            res = await self.call("users.conversations", **params)
            for c in res.get("channels") or []:
                if c.get("is_im") or c.get("is_mpim") or c.get("id") in wanted or c.get("name") in wanted:
                    convs.append(c)
            cursor = (res.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        convs = convs[: int(self.options.get("max_conversations", 50))]
        self._convs = (now, convs)
        return convs

    async def _user(self, uid: str | None) -> dict[str, Any]:
        if not uid:
            return {}
        if uid not in self._users:
            try:
                u = (await self.call("users.info", user=uid)).get("user") or {}
            except ApiError:
                u = {}
            prof = u.get("profile") or {}
            self._users[uid] = {
                "name": prof.get("display_name") or u.get("real_name") or u.get("name") or uid,
                "email": prof.get("email"),
            }
        return self._users[uid]

    async def _normalize(self, conv: dict[str, Any], m: dict[str, Any], me: str | None) -> InboundEvent:
        ctype = _channel_type(conv)
        who = await self._user(m.get("user"))
        where = {"im": "DM", "mpim": "group DM"}.get(ctype) or f"#{conv.get('name') or conv['id']}"
        text = m.get("text") or ""
        mentions = bool(me and f"<@{me}>" in text)
        return InboundEvent(
            source=self.name,
            kind="chat",
            external_id=f"{conv['id']}:{m['ts']}",
            title=f"{who.get('name') or 'Someone'} in {where}: {' '.join(text.split())[:100]}",
            body=text,
            sender=who.get("email") or m.get("user"),
            occurred_at=_ts(m.get("ts")),
            data={
                "channel": conv["id"],
                "channel_name": conv.get("name"),
                "channel_type": ctype,
                "ts": m["ts"],
                "thread_ts": m.get("thread_ts"),
                "user": m.get("user"),
                "mentions_me": mentions,
                "direct": ctype in ("im", "mpim") or mentions,
            },
        )

    async def fetch_new(self, hint: dict[str, Any] | None = None) -> list[InboundEvent]:
        convs = await self._conversations()
        if hint and hint.get("channel") and not any(c["id"] == hint["channel"] for c in convs):
            info = (await self.call("conversations.info", channel=hint["channel"])).get("channel") or {}
            if info.get("id"):
                convs = [*convs, info]
        cursors: dict[str, str] = dict(self.kv_get("ts") or {})
        first = str((self.clock.now() - timedelta(days=1)).timestamp())
        me = self.self_id()
        events: list[InboundEvent] = []
        for conv in convs:
            oldest = cursors.get(conv["id"], first)
            res = await self.call("conversations.history", channel=conv["id"], oldest=oldest, limit=50)
            msgs = res.get("messages") or []
            for m in reversed(msgs):  # oldest first
                if m.get("subtype") not in KEEP_SUBTYPES or not m.get("ts"):
                    continue
                if me and m.get("user") == me:
                    continue
                events.append(await self._normalize(conv, m, me))
            if msgs:
                cursors[conv["id"]] = max((m["ts"] for m in msgs if m.get("ts")), key=float)
        self.kv_set("ts", cursors)
        return events

    # ---- tools ----
    def tools(self) -> list[Tool]:
        n = self.name
        return [
            self.tool(
                f"{n}_search",
                "Search Slack messages the user can see (Slack search syntax, e.g. 'invoice in:#finance').",
                schema({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
                self._search,
                title="Search Slack",
            ),
            self.tool(
                f"{n}_history",
                "Recent messages in a Slack channel or DM (channel id, e.g. C0123 or D0123).",
                schema({"channel": {"type": "string"}, "limit": {"type": "integer"}}, ["channel"]),
                self._history,
                title="Read Slack channel",
            ),
            self.tool(
                f"{n}_post",
                "Post a Slack message as the user (optionally in a thread).",
                schema(
                    {
                        "channel": {"type": "string"},
                        "text": {"type": "string"},
                        "thread_ts": {"type": "string"},
                    },
                    ["channel", "text"],
                ),
                self._post,
                tags=frozenset({"send", "external_post"}),
                editable=("text",),
                summarize=lambda a: (
                    f"Post in Slack {a.get('channel')}: {' '.join(str(a.get('text', '')).split())[:80]}"
                ),
                title="Post to Slack",
            ),
        ]

    async def _search(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        res = await self.call(
            "search.messages",
            query=args["query"],
            count=min(int(args.get("limit") or 10), 50),
            sort="timestamp",
        )
        matches = (res.get("messages") or {}).get("matches") or []
        return {
            "messages": [
                {
                    "channel": (m.get("channel") or {}).get("id"),
                    "channel_name": (m.get("channel") or {}).get("name"),
                    "user": m.get("username") or m.get("user"),
                    "text": m.get("text"),
                    "ts": m.get("ts"),
                    "permalink": m.get("permalink"),
                }
                for m in matches
            ]
        }

    async def _history(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        res = await self.call(
            "conversations.history", channel=args["channel"], limit=min(int(args.get("limit") or 20), 100)
        )
        out = []
        for m in res.get("messages") or []:
            who = await self._user(m.get("user"))
            out.append(
                {
                    "user": who.get("name") or m.get("user"),
                    "text": m.get("text"),
                    "ts": m.get("ts"),
                    "thread_ts": m.get("thread_ts"),
                }
            )
        return {"channel": args["channel"], "messages": out}

    async def _post(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        body = {"channel": args["channel"], "text": args["text"]}
        if args.get("thread_ts"):
            body["thread_ts"] = args["thread_ts"]
        res = await self._api(
            "POST",
            f"{API}/chat.postMessage",
            json=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        return {"posted": True, "channel": res.get("channel"), "ts": res.get("ts")}
