"""The one thread: answers the user with the chat model and hands real work to tasks."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from tandem.engine import prompts
from tandem.models.base import Message
from tandem.telemetry import tracer
from tandem.tools import GatedTools, ToolContext
from tandem.util import new_id

log = logging.getLogger("tandem.chat")
CHAT_TOOLS = ("start_task", "today_summary", "approvals_list", "memory_search", "memory_write", "skill_load")


class ChatAgent:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self._lock = asyncio.Lock()

    def tools(self) -> list[Any]:
        reg = self.rt.registry
        chosen = [reg.tools[n] for n in CHAT_TOOLS if n in reg.tools]
        # Read-only connector tools let the chat answer quick questions without starting a task.
        chosen += [
            t
            for t in reg.tools.values()
            if t.tags == frozenset({"read"})
            and t.name not in CHAT_TOOLS
            and not t.name.startswith("sandbox_")
        ]
        return chosen

    def state_line(self) -> str:
        s = self.rt.store
        pending = len(s.list_approvals(state="pending"))
        running = len(s.list_tasks(state=["queued", "running", "waiting"]))
        return f"{running} tasks in progress, {pending} approvals waiting."

    def history(self) -> list[Message]:
        window = self.rt.cfg.memory.thread_window
        out = []
        for m in self.rt.store.list_messages(limit=window):
            if m["role"] == "user":
                out.append(Message("user", m["text"]))
            elif m["role"] == "agent" and m["text"]:
                out.append(Message("assistant", m["text"]))
        return out

    async def reply(self, text: str, harness: str | None = None) -> dict[str, Any] | None:
        with tracer().agent_span("chat", new_id("chat")):
            return await self._reply(text, harness)

    async def _reply(self, text: str, harness: str | None = None) -> dict[str, Any] | None:
        rt = self.rt
        async with self._lock:
            rt.store.bus.publish("typing", {"on": True})
            try:
                persona = prompts.PERSONA.format(user=rt.cfg.user_name or "the user")
                system = prompts.CHAT.format(
                    persona=persona,
                    core=rt.memory.core_block(),
                    state=self.state_line(),
                    rules=prompts.RULES,
                    extra=rt.cfg.persona,
                )
                if harness:
                    system += f"\nIf you start a task, use harness {harness!r}."
                messages = [Message("system", system), *self.history()]
                if messages[-1].content != text:
                    messages.append(Message("user", text))
                ctx = ToolContext(origin="chat", rt=rt)
                gated = GatedTools(rt.registry, self.tools(), ctx)
                model = rt.models.for_role("chat")
                final = ""
                for _ in range(6):
                    resp = await model.complete(messages, gated.specs())
                    if not resp.tool_calls:
                        final = resp.text.strip()
                        break
                    messages.append(Message("assistant", resp.text, tool_calls=resp.tool_calls))
                    for call in resp.tool_calls:
                        args = dict(call.args)
                        if call.name == "start_task" and harness and not args.get("harness"):
                            args["harness"] = harness
                        outcome = await gated.invoke(call.name, args, call.id)
                        messages.append(Message("tool", outcome.text, tool_call_id=call.id, name=call.name))
                if not final:
                    final = "I've set that in motion."
                return rt.store.add_message("agent", rt.redactor.text(final))
            except Exception as exc:
                log.exception("chat reply failed")
                return rt.store.add_message(
                    "system", f"Sorry, I hit an error: {rt.redactor.text(str(exc))[:500]}"
                )
            finally:
                rt.store.bus.publish("typing", {"on": False})

    async def summarize_old(self) -> None:
        """Fold messages older than the window into a thread_summary memory entry."""
        rt = self.rt
        window = rt.cfg.memory.thread_window
        total = len(rt.store.list_messages(limit=10_000))
        done_upto = int(rt.store.kv_get("summarized_upto", 0) or 0)
        if total - done_upto <= window * 2:
            return
        old = rt.store.list_messages(limit=total - window)[done_upto:]
        if not old:
            return
        payload = {
            "kind": "summarize",
            "messages": [f"{m['role']}: {m['text']}" for m in old if m["text"]][-200:],
        }
        try:
            out = await rt.models.for_role("chat").complete_json(
                [
                    Message(
                        "system",
                        "Summarize this conversation segment in under 120 words: decisions, preferences, open items. "
                        'Reply with JSON {"summary": "..."}',
                    ),
                    Message("user", "<data>" + json.dumps(payload, ensure_ascii=False) + "</data>"),
                ]
            )
            rt.memory.write(str(out.get("summary", ""))[:2000], "thread_summary", source="system")
            rt.store.kv_set("summarized_upto", done_upto + len(old))
        except Exception as exc:
            log.warning("thread summary failed: %s", exc)
