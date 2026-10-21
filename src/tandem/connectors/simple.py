"""Webhook inbox and filesystem connectors.

```yaml
- type: webhook
  name: inbox                # POST /api/webhooks/inbox
  secret_env: INBOX_WEBHOOK_SECRET   # optional: require X-Tandem-Signature: sha256=<hmac of body>
- type: filesystem
  name: files                # tools: files_list, files_read, files_write
  root: ~/Documents/inbox
  watch: true                # new or changed files become events
```
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tandem.connectors.base import Connector, InboundEvent, register_connector
from tandem.tools import Tool, ToolContext, schema


class WebhookAuthError(PermissionError):
    pass


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@register_connector
class WebhookConnector(Connector):
    type = "webhook"

    def _secret(self) -> str | None:
        return (
            self.secrets.get(self.options.get("secret_env"))
            if self.secrets and self.options.get("secret_env")
            else None
        )

    async def handle_webhook(
        self, payload: dict[str, Any], headers: dict[str, str], raw: bytes = b""
    ) -> list[InboundEvent]:
        secret = self._secret()
        if secret:
            given = headers.get("x-tandem-signature", "")
            if not hmac.compare_digest(given, sign(secret, raw or json.dumps(payload).encode())):
                raise WebhookAuthError("bad or missing X-Tandem-Signature")
        items = payload.get("events") if isinstance(payload.get("events"), list) else [payload]
        out = []
        for item in items:
            title = str(
                item.get("title")
                or item.get("subject")
                or item.get("summary")
                or item.get("text")
                or "Webhook event"
            )
            body = item.get("body") or item.get("message") or item.get("description") or ""
            if not isinstance(body, str):
                body = json.dumps(body)
            if not body and not any(k in item for k in ("title", "subject", "summary", "text")):
                body = json.dumps(item)[:4000]
            out.append(
                InboundEvent(
                    source=self.name,
                    kind=str(item.get("kind") or "webhook"),
                    external_id=str(item.get("id") or item.get("external_id") or uuid.uuid4().hex),
                    title=title,
                    body=body,
                    sender=item.get("sender") or item.get("from"),
                    occurred_at=self.clock.now(),
                    data={k: v for k, v in item.items() if k not in ("body",)},
                )
            )
        self.last_poll = self.clock.now()
        return out


@register_connector
class FilesystemConnector(Connector):
    type = "filesystem"

    def __init__(self, name: str, options: dict[str, Any], clock: Any, secrets: Any = None) -> None:
        super().__init__(name, options, clock, secrets)
        self.root = Path(str(options.get("root", "."))).expanduser().resolve()
        self._seen: dict[str, float] = {}
        self._primed = False

    def _resolve(self, rel: str) -> Path:
        p = (self.root / (rel or ".").lstrip("/\\")).resolve()
        if p != self.root and self.root not in p.parents:
            raise PermissionError(f"path {rel!r} is outside {self.root}")
        return p

    async def poll(self, since: datetime | None) -> list[InboundEvent]:
        self.last_poll = self.clock.now()
        if not self.options.get("watch", True) or not self.root.is_dir():
            return []
        out = []
        for p in self.root.rglob("*"):
            if not p.is_file() or any(part.startswith(".") for part in p.relative_to(self.root).parts):
                continue
            rel, mtime = p.relative_to(self.root).as_posix(), p.stat().st_mtime
            if self._seen.get(rel) != mtime:
                if self._primed or self.options.get("report_existing", False):
                    out.append(
                        InboundEvent(
                            source=self.name,
                            kind="file",
                            external_id=f"{rel}@{mtime}",
                            title=f"{'Changed' if rel in self._seen else 'New'} file: {rel}",
                            body=f"{p.stat().st_size} bytes",
                            occurred_at=datetime.fromtimestamp(mtime, UTC),
                            data={"path": rel},
                        )
                    )
                self._seen[rel] = mtime
        self._primed = True
        return out

    def tools(self) -> list[Tool]:
        return [
            Tool(
                f"{self.name}_list",
                f"List files under {self.root.name}/.",
                schema({"path": {"type": "string"}}),
                self._list,
                title="List files",
            ),
            Tool(
                f"{self.name}_read",
                "Read a text file.",
                schema({"path": {"type": "string"}}, ["path"]),
                self._read,
                title="Read file",
            ),
            Tool(
                f"{self.name}_write",
                "Write a text file (creates folders).",
                schema({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
                self._write,
                tags=frozenset({"write"}),
                summarize=lambda a: f"Write {a.get('path')}",
                title="Write file",
            ),
        ]

    async def _list(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        base = self._resolve(args.get("path") or ".")
        return {
            "files": [p.relative_to(self.root).as_posix() for p in sorted(base.rglob("*")) if p.is_file()][
                :500
            ]
        }

    async def _read(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return self._resolve(args["path"]).read_text(encoding="utf-8", errors="replace")[:100_000]

    async def _write(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        p = self._resolve(args["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"], encoding="utf-8")
        return {"written": p.relative_to(self.root).as_posix(), "bytes": p.stat().st_size}
