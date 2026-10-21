"""IMAP (read) + SMTP (send) email with the standard library. Credentials come from env vars only.

```yaml
- type: imap
  name: email                # tools: email_search, email_read, email_send
  host: imap.example.com
  port: 993
  username: me@example.com
  password_env: IMAP_PASSWORD
  folder: INBOX
  smtp_host: smtp.example.com
  smtp_port: 587             # STARTTLS; 465 for implicit TLS
  from: me@example.com
```
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import imaplib
import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from typing import Any

from tandem.connectors.base import Connector, InboundEvent, register_connector
from tandem.tools import Tool, ToolContext, schema


def _body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                return (
                    part.get_content()
                    if hasattr(part, "get_content")
                    else str(part.get_payload(decode=True), "utf-8", "replace")
                )
        return ""
    try:
        return msg.get_content()  # type: ignore[attr-defined]
    except Exception:
        payload = msg.get_payload(decode=True)
        return payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload)


def parse_message(raw: bytes, uid: str, source: str) -> InboundEvent:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    try:
        when = parsedate_to_datetime(msg["Date"]) if msg["Date"] else datetime.now(UTC)
    except (TypeError, ValueError):
        when = datetime.now(UTC)
    headers = {
        k: str(v) for k, v in msg.items() if k.lower() in ("list-unsubscribe", "precedence", "auto-submitted")
    }
    return InboundEvent(
        source=source,
        kind="email",
        external_id=str(msg.get("Message-ID") or uid),
        title=str(msg.get("Subject") or "(no subject)"),
        body=_body(msg)[:20000],
        sender=email.utils.parseaddr(str(msg.get("From") or ""))[1],
        occurred_at=when,
        data={"uid": uid, "headers": headers},
    )


@register_connector
class ImapConnector(Connector):
    type = "imap"

    @property
    def send_tool(self) -> str | None:  # type: ignore[override]
        return f"{self.name}_send" if self.options.get("smtp_host") else None

    def _password(self) -> str:
        pw = self.secrets.get(self.options.get("password_env")) if self.secrets else None
        if not pw:
            raise RuntimeError(
                f"set {self.options.get('password_env') or 'password_env'} for connector {self.name!r}"
            )
        return pw

    def _imap(self) -> imaplib.IMAP4:
        host, port = self.options["host"], int(self.options.get("port", 993))
        conn: imaplib.IMAP4 = (
            imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context())
            if self.options.get("ssl", True)
            else imaplib.IMAP4(host, port)
        )
        conn.login(self.options["username"], self._password())
        conn.select(self.options.get("folder", "INBOX"), readonly=True)
        return conn

    def _fetch(self, criteria: str, limit: int) -> list[InboundEvent]:
        conn = self._imap()
        try:
            typ, data = conn.uid("search", None, criteria)  # type: ignore[arg-type]
            uids = (data[0] or b"").split()[-limit:]
            out = []
            for uid in reversed(uids):
                typ, msg_data = conn.uid("fetch", uid, "(BODY.PEEK[])")
                if typ == "OK" and msg_data and isinstance(msg_data[0], tuple):
                    out.append(parse_message(msg_data[0][1], uid.decode(), self.name))
            return out
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    async def poll(self, since: datetime | None) -> list[InboundEvent]:
        crit = "UNSEEN"
        if since:
            crit = f'(UNSEEN SINCE "{since.strftime("%d-%b-%Y")}")'
        try:
            events = await asyncio.to_thread(self._fetch, crit, int(self.options.get("max_per_poll", 50)))
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)
            raise
        finally:
            self.last_poll = self.clock.now()
        return events

    def tools(self) -> list[Tool]:
        tools = [
            Tool(
                f"{self.name}_search",
                "Search the mailbox (IMAP TEXT search). Returns id, from, subject, snippet.",
                schema({"query": {"type": "string"}, "limit": {"type": "integer"}}),
                self._search,
                title="Search email",
            ),
            Tool(
                f"{self.name}_read",
                "Read one email by id.",
                schema({"id": {"type": "string"}}, ["id"]),
                self._read,
                title="Read email",
            ),
        ]
        if self.options.get("smtp_host"):
            tools.append(
                Tool(
                    f"{self.name}_send",
                    "Send an email from the user's account (SMTP).",
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
                )
            )
        return tools

    async def _search(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        q = (args.get("query") or "").replace('"', "")
        events = await asyncio.to_thread(
            self._fetch, f'TEXT "{q}"' if q else "ALL", int(args.get("limit") or 10)
        )
        return {
            "messages": [
                {
                    "id": e.data["uid"],
                    "from": e.sender,
                    "subject": e.title,
                    "snippet": " ".join(e.body.split())[:140],
                }
                for e in events
            ]
        }

    async def _read(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        events = await asyncio.to_thread(self._fetch, f"UID {args['id']}", 1)
        if not events:
            raise KeyError(f"no email {args['id']!r}")
        e = events[0]
        return {"id": args["id"], "from": e.sender, "subject": e.title, "body": e.body}

    def _smtp_send(self, msg: EmailMessage) -> None:
        host, port = self.options["smtp_host"], int(self.options.get("smtp_port", 587))
        user = self.options.get("smtp_username", self.options.get("username"))
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as s:
                s.login(user, self._password())
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(user, self._password())
                s.send_message(msg)

    async def _send(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        msg = EmailMessage()
        msg["From"] = self.options.get("from", self.options.get("username"))
        msg["To"] = args["to"]
        if args.get("cc"):
            msg["Cc"] = args["cc"]
        msg["Subject"] = args["subject"]
        msg.set_content(args["body"])
        await asyncio.to_thread(self._smtp_send, msg)
        return {"sent": True, "to": args["to"], "subject": args["subject"]}
