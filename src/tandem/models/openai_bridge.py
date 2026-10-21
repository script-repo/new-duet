"""An in-process OpenAI Chat Completions endpoint backed by any Tandem `ModelClient`.

Frameworks that only speak the OpenAI API (the OpenAI Agents SDK, for one) get every Tandem provider
this way, mock mode included: give their `AsyncOpenAI` client an `httpx.AsyncClient` with this
transport. Nothing listens on a port; requests never leave the process.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx

from tandem.models.base import Message, ModelClient, ToolCall, ToolSpec, loads_args


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return "" if content is None else str(content)


def to_messages(raw: list[dict[str, Any]]) -> list[Message]:
    names: dict[str, str] = {}
    out: list[Message] = []
    for m in raw:
        role = m.get("role")
        if role in ("system", "developer"):
            out.append(Message("system", _text(m.get("content"))))
        elif role == "user":
            out.append(Message("user", _text(m.get("content"))))
        elif role == "assistant":
            calls = []
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                names[tc.get("id", "")] = fn.get("name", "")
                calls.append(ToolCall(tc.get("id", ""), fn.get("name", ""), loads_args(fn.get("arguments"))))
            out.append(Message("assistant", _text(m.get("content")), tool_calls=calls))
        elif role == "tool":
            cid = m.get("tool_call_id")
            out.append(Message("tool", _text(m.get("content")), tool_call_id=cid, name=names.get(cid or "")))
    return out


class ChatCompletionsBridge(httpx.AsyncBaseTransport):
    def __init__(self, client: ModelClient) -> None:
        self.client = client

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                404,
                json={"error": {"message": f"bridge serves chat/completions only, not {request.url.path}"}},
            )
        body = json.loads(await request.aread() or b"{}")
        if body.get("stream"):
            return httpx.Response(
                400, json={"error": {"message": "streaming is not supported by the Tandem bridge"}}
            )
        tools = [
            ToolSpec(
                t["function"]["name"],
                t["function"].get("description", ""),
                t["function"].get("parameters") or {},
            )
            for t in body.get("tools") or []
            if t.get("type") == "function"
        ]
        json_mode = (body.get("response_format") or {}).get("type") in ("json_object", "json_schema")
        resp = await self.client.complete(
            to_messages(body.get("messages") or []), tools or None, json_mode=json_mode
        )
        message: dict[str, Any] = {"role": "assistant", "content": resp.text or None}
        if resp.tool_calls:
            message["tool_calls"] = [
                {
                    "id": c.id or f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.args)},
                }
                for c in resp.tool_calls
            ]
        return httpx.Response(
            200,
            json={
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.get("model") or self.client.profile.model,
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if resp.tool_calls else "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": resp.usage.input_tokens,
                    "completion_tokens": resp.usage.output_tokens,
                    "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens,
                },
            },
        )
