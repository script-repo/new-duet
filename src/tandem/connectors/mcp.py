"""Any MCP server as a connector (official `mcp` SDK, extra `[mcp]`), over stdio or streamable HTTP.

```yaml
- type: mcp
  name: notion                       # tools become notion_<tool>
  command: npx                       # stdio...
  args: ["-y", "@notionhq/notion-mcp-server"]
  env_from: [NOTION_TOKEN]           # host env vars passed to the server (secrets stay server-side)
  # url: https://mcp.example.com/mcp # ...or streamable HTTP
  # headers_from: {Authorization: MCP_AUTH_HEADER}
  ask: ["*create*", "*update*", "*delete*"]   # extra globs that always need approval
  poll_tool: search                  # optional: call on each check-in, results become events
  poll_args: {query: "is:unread"}
```

Each call opens a short session: simpler and robust across event loops; fine for check-in rates.
Tool tags come from MCP annotations: readOnlyHint → read, destructiveHint → delete, openWorldHint →
external_post; anything not read-only is tagged `write`.
"""

from __future__ import annotations

import contextlib
import fnmatch
import json
import os
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from tandem.connectors.base import Connector, InboundEvent, register_connector
from tandem.tools import Tool, ToolContext


def tags_for(tool_name: str, annotations: Any, ask_globs: list[str]) -> frozenset[str]:
    tags: set[str] = set()
    get = (
        (lambda k: getattr(annotations, k, None))
        if annotations is not None and not isinstance(annotations, dict)
        else (lambda k: (annotations or {}).get(k))
    )
    if get("readOnlyHint"):
        tags.add("read")
    else:
        tags.add("write")
    if get("destructiveHint"):
        tags.add("delete")
    if get("openWorldHint") and not get("readOnlyHint"):
        tags.add("external_post")
    if any(fnmatch.fnmatchcase(tool_name, g) for g in ask_globs):
        tags.add("external_post")
    return frozenset(tags)


def content_text(result: Any) -> str:
    parts = []
    for c in getattr(result, "content", None) or []:
        text = getattr(c, "text", None)
        if text is None:
            dump = getattr(c, "model_dump", None)
            text = json.dumps(dump() if dump else str(c), default=str)
        parts.append(text)
    structured = getattr(result, "structuredContent", None)
    if not parts and structured is not None:
        parts.append(json.dumps(structured, default=str))
    return "\n".join(parts)


@register_connector
class McpConnector(Connector):
    type = "mcp"

    def __init__(self, name: str, options: dict[str, Any], clock: Any, secrets: Any = None) -> None:
        super().__init__(name, options, clock, secrets)
        self._tools: list[Tool] = []

    def _env(self) -> dict[str, str]:
        env = {"PATH": os.environ.get("PATH", "")}
        for key in self.options.get("env_from", []):
            val = self.secrets.get(key) if self.secrets else os.environ.get(key)
            if val:
                env[key] = val
        env.update({k: str(v) for k, v in (self.options.get("env") or {}).items()})
        return env

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[Any]:
        try:
            from mcp import ClientSession, StdioServerParameters
        except ImportError as exc:
            raise RuntimeError("install the MCP extra: uv sync --extra mcp") from exc
        if self.options.get("url"):
            from mcp.client.streamable_http import streamablehttp_client

            headers = {
                h: (self.secrets.get(env) if self.secrets else os.environ.get(env)) or ""
                for h, env in (self.options.get("headers_from") or {}).items()
            }
            async with (
                streamablehttp_client(self.options["url"], headers=headers) as (read, write, _),
                ClientSession(read, write) as s,
            ):
                await s.initialize()
                yield s
        else:
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=self.options["command"], args=list(self.options.get("args", [])), env=self._env()
            )
            async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
                await s.initialize()
                yield s

    async def start(self) -> None:
        try:
            async with self.session() as s:
                listed = await s.list_tools()
            self._tools = [self._wrap(t) for t in listed.tools]
            self.last_error = None
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"

    def _wrap(self, t: Any) -> Tool:
        remote = t.name

        async def call(args: dict[str, Any], ctx: ToolContext) -> str:
            async with self.session() as s:
                result = await s.call_tool(remote, args)
            text = content_text(result)
            if getattr(result, "isError", False):
                raise RuntimeError(text or "MCP tool error")
            return text

        schema = dict(getattr(t, "inputSchema", None) or {"type": "object", "properties": {}})
        return Tool(
            name=f"{self.name}_{remote}"[:64],
            description=(t.description or remote)[:1000],
            parameters=schema,
            func=call,
            tags=tags_for(remote, getattr(t, "annotations", None), list(self.options.get("ask", []))),
            editable=tuple((schema.get("properties") or {}).keys()),
            title=f"{self.name}: {remote}",
        )

    def tools(self) -> list[Tool]:
        return list(self._tools)

    async def poll(self, since: datetime | None) -> list[InboundEvent]:
        tool = self.options.get("poll_tool")
        self.last_poll = self.clock.now()
        if not tool:
            return []
        async with self.session() as s:
            result = await s.call_tool(tool, dict(self.options.get("poll_args") or {}))
        text = content_text(result)
        try:
            items = json.loads(text)
        except json.JSONDecodeError:
            items = [{"title": line[:200]} for line in text.splitlines() if line.strip()]
        if isinstance(items, dict):
            items = items.get("results") or items.get("items") or [items]
        out = []
        for i, item in enumerate(items[:50]):
            if not isinstance(item, dict):
                item = {"title": str(item)}
            out.append(
                InboundEvent(
                    source=self.name,
                    kind="mcp",
                    external_id=str(item.get("id") or f"{hash(json.dumps(item, default=str))}-{i}"),
                    title=str(item.get("title") or item.get("name") or item.get("subject") or "Item")[:300],
                    body=json.dumps(item, default=str)[:4000],
                    occurred_at=self.clock.now(),
                    data=item,
                )
            )
        return out
