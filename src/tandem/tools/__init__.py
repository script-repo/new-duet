"""Tools and the gate every call passes through (SPEC §3.5): validate → policy → approval → run →
redact → audit. Harness adapters only see `GatedTools`, so gating is identical for every harness.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from tandem.models.base import ToolSpec
from tandem.policy import Policy
from tandem.security import Redactor
from tandem.store import Store
from tandem.telemetry import tracer
from tandem.util import iso, to_text, truncate

if TYPE_CHECKING:
    from tandem.sandbox import Sandbox

log = logging.getLogger("tandem.tools")


@dataclass
class ToolContext:
    task_id: str | None = None
    origin: str = "chat"
    sandbox: Sandbox | None = None
    rt: Any = None  # the Runtime, for tools that need services (artifacts, memory, tasks)
    actor: str = "agent"
    on_approval: Callable[[dict[str, Any]], None] | None = None  # told when a call waits on a card


ToolFunc = Callable[[dict[str, Any], ToolContext], Awaitable[Any]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    func: ToolFunc
    tags: frozenset[str] = frozenset({"read"})
    editable: tuple[str, ...] = ()
    summarize: Callable[[dict[str, Any]], str] | None = None
    title: str | None = None  # short human label for cards ("Send email")

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.parameters)

    def card_title(self, args: dict[str, Any]) -> str:
        if self.summarize:
            with contextlib.suppress(Exception):
                return self.summarize(args)
        return self.title or self.name.replace("_", " ").capitalize()


def schema(properties: dict[str, Any], required: Iterable[str] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required)}


_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def validate_args(params: dict[str, Any], args: dict[str, Any]) -> list[str]:
    errors = []
    props = params.get("properties", {})
    for key in params.get("required", []):
        if key not in args or args[key] is None:
            errors.append(f"missing required argument {key!r}")
    for key, val in args.items():
        spec = props.get(key)
        if not spec or "type" not in spec or val is None:
            continue
        types = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        py: list[type] = []
        for type_name in types:
            t = _TYPES.get(type_name, object)
            py.extend(t if isinstance(t, tuple) else (t,))
        if (isinstance(val, bool) and "boolean" not in types) or not isinstance(val, tuple(py)):
            errors.append(f"argument {key!r} should be {'/'.join(types)}")
    return errors


@dataclass
class ToolOutcome:
    ok: bool
    text: str
    decision: str  # allow | approved | edited | rejected | expired | denied | error | invalid
    value: Any = None
    approval_id: str | None = None


# ---- approvals --------------------------------------------------------------------------------------------


@dataclass
class _Pending:
    future: asyncio.Future[tuple[str, dict[str, Any] | None, str | None]]


class ApprovalService:
    """Pending approvals and their decisions. Silence never approves: expiry is a denial."""

    def __init__(self, store: Store, policy: Policy) -> None:
        self.store = store
        self.policy = policy
        self._pending: dict[str, _Pending] = {}
        self.on_created: list[Callable[[dict[str, Any], ToolContext], Awaitable[None] | None]] = []
        self.on_edit: list[Callable[[dict[str, Any], dict[str, Any]], Awaitable[None] | None]] = []

    async def request(
        self, tool: Tool, args: dict[str, Any], ctx: ToolContext, reason: str, redactor: Redactor
    ) -> dict[str, Any]:
        now = self.store.clock.now()
        ap = self.store.create_approval(
            tool=tool.name,
            title=redactor.text(tool.card_title(args)),
            summary=redactor.text(tool.description.split(".")[0]),
            reason=reason,
            args=redactor.value(args),
            editable=list(tool.editable),
            tags=sorted(tool.tags),
            task_id=ctx.task_id,
            expires_at=iso(now + timedelta(seconds=self.policy.timeout_s)),
        )
        loop = asyncio.get_running_loop()
        self._pending[ap["id"]] = _Pending(loop.create_future())
        for cb in self.on_created:
            res = cb(ap, ctx)
            if asyncio.iscoroutine(res):
                await res
        return ap

    async def wait(self, approval_id: str) -> tuple[str, dict[str, Any] | None, str | None]:
        pending = self._pending.get(approval_id)
        if not pending:
            ap = self.store.get_approval(approval_id)
            return (ap["state"] if ap else "expired"), None, None
        try:
            # Belt and braces: the expiry job resolves this, but never wait past the policy timeout.
            return await asyncio.wait_for(asyncio.shield(pending.future), timeout=self.policy.timeout_s + 30)
        except TimeoutError:
            self._resolve(approval_id, "expired", None, "timed out")
            return "expired", None, "timed out"
        finally:
            self._pending.pop(approval_id, None)

    def _resolve(self, approval_id: str, state: str, args: dict[str, Any] | None, note: str | None) -> None:
        pending = self._pending.get(approval_id)
        if pending and not pending.future.done():
            pending.future.set_result((state, args, note))

    async def decide(
        self,
        approval_id: str,
        decision: str,
        args: dict[str, Any] | None = None,
        note: str | None = None,
        by: str = "user",
    ) -> dict[str, Any]:
        ap = self.store.get_approval(approval_id)
        if not ap:
            raise KeyError(approval_id)
        if ap["state"] != "pending":
            raise ValueError(f"approval is already {ap['state']}")
        if decision not in ("approve", "reject", "edit"):
            raise ValueError("decision must be approve, reject or edit")
        new_args = None
        if decision == "edit":
            editable = set(ap.get("editable") or [])
            # Clients may send the full argument set; only changed values must be editable.
            bad = {k for k, v in (args or {}).items() if k not in editable and ap["args"].get(k) != v}
            if bad:
                raise ValueError(f"these arguments are not editable: {', '.join(sorted(bad))}")
            new_args = {**ap["args"], **(args or {})}
            for cb in self.on_edit:
                res = cb(ap, new_args)
                if asyncio.iscoroutine(res):
                    await res
        state = {"approve": "approved", "edit": "approved", "reject": "rejected"}[decision]
        updated = self.store.update_approval(
            approval_id,
            state=state,
            decided_at=self.store.now(),
            decided_by=by,
            note=note,
            **({"args": new_args} if new_args else {}),
        )
        self._resolve(approval_id, "edited" if decision == "edit" else state, new_args, note)
        assert updated
        return updated

    def expire_due(self) -> list[dict[str, Any]]:
        now = self.store.now()
        expired = []
        for ap in self.store.list_approvals(state="pending"):
            if ap["expires_at"] <= now:
                expired.append(
                    self.store.update_approval(
                        ap["id"], state="expired", decided_at=now, decided_by="timeout"
                    )
                )
                self._resolve(ap["id"], "expired", None, "timed out")
        return expired

    def cancel(self, approval_id: str, note: str = "cancelled") -> None:
        ap = self.store.get_approval(approval_id)
        if ap and ap["state"] == "pending":
            self.store.update_approval(
                approval_id, state="cancelled", decided_at=self.store.now(), decided_by="system", note=note
            )
        self._resolve(approval_id, "cancelled", None, note)

    def cancel_for_task(self, task_id: str) -> None:
        for ap in self.store.list_approvals(state="pending", task_id=task_id):
            self.cancel(ap["id"], "task stopped")

    def cancel_orphans(self) -> None:
        """At startup: approvals left pending by a previous process can never resume."""
        for ap in self.store.list_approvals(state="pending"):
            if ap["id"] not in self._pending:
                self.store.update_approval(
                    ap["id"],
                    state="cancelled",
                    decided_at=self.store.now(),
                    decided_by="system",
                    note="server restarted",
                )


# ---- registry ---------------------------------------------------------------------------------------------


async def traced_call(
    name: str,
    args: dict[str, Any] | None,
    ctx: ToolContext,
    call_id: str | None,
    run: Callable[[], Awaitable[ToolOutcome]],
) -> ToolOutcome:
    """Run one tool call inside an `execute_tool` span (a no-op unless telemetry is on)."""
    t = tracer()
    attrs = {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": name,
        "gen_ai.tool.type": "function",
        "gen_ai.tool.call.id": call_id,
        "tandem.task.id": ctx.task_id,
    }
    with t.span(f"execute_tool {name}", attrs) as sp:
        outcome = await run()
        if sp is not None:
            sp.set("gen_ai.tool.call.arguments", t.content(args or {}))
            sp.set("gen_ai.tool.call.result", t.content(outcome.text))
            sp.set("tandem.approval.decision", outcome.decision)
            if not outcome.ok:
                sp.set("error.type", outcome.decision)  # error | invalid | denied | rejected | expired
                sp.error(outcome.text)
        return outcome


@dataclass
class ToolRegistry:
    store: Store
    policy: Policy
    approvals: ApprovalService
    redactor: Redactor
    tools: dict[str, Tool] = field(default_factory=dict)
    result_limit: int = 12000

    def add(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def extend(self, tools: Iterable[Tool]) -> None:
        for t in tools:
            self.add(t)

    def remove_prefix(self, prefix: str) -> None:
        for name in [n for n in self.tools if n.startswith(prefix)]:
            del self.tools[name]

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def select(self, names: Iterable[str] | None = None, exclude_tags: Iterable[str] = ()) -> list[Tool]:
        ex = set(exclude_tags)
        pool = (
            [self.tools[n] for n in names if n in self.tools]
            if names is not None
            else list(self.tools.values())
        )
        return [t for t in pool if not (t.tags & ex)]

    def _audit(
        self,
        tool: str,
        args: dict[str, Any],
        decision: str,
        ctx: ToolContext,
        ok: bool | None,
        detail: str = "",
        approval_id: str | None = None,
    ) -> None:
        self.store.add_audit(
            actor=ctx.actor,
            tool=tool,
            args=self.redactor.value(args),
            decision=decision,
            approval_id=approval_id,
            task_id=ctx.task_id,
            ok=None if ok is None else int(ok),
            detail=self.redactor.text(detail)[:2000],
        )

    async def invoke(
        self, name: str, args: dict[str, Any] | None, ctx: ToolContext, call_id: str | None = None
    ) -> ToolOutcome:
        return await traced_call(name, args, ctx, call_id, lambda: self._invoke(name, args, ctx))

    async def _invoke(self, name: str, args: dict[str, Any] | None, ctx: ToolContext) -> ToolOutcome:
        args = dict(args or {})
        tool = self.tools.get(name)
        if tool is None:
            return ToolOutcome(
                False, f"Unknown tool {name!r}. Available: {', '.join(sorted(self.tools))}", "invalid"
            )
        errors = validate_args(tool.parameters, args)
        if errors:
            return ToolOutcome(False, "Invalid arguments: " + "; ".join(errors), "invalid")

        sandbox_kind = ctx.sandbox.kind if ctx.sandbox else None
        verdict = self.policy.evaluate(tool.name, tool.tags, args, sandbox_kind, ctx.origin)
        decision = "allow"
        approval_id = None
        if verdict.action == "deny":
            self._audit(name, args, "denied", ctx, None, verdict.reason)
            self.store.add_activity(
                "tool", f"Blocked by policy: {tool.card_title(args)}", verdict.reason, ctx.task_id, "warn"
            )
            return ToolOutcome(
                False,
                f"Blocked by policy: {verdict.reason or 'this action is not allowed'}. Do not retry.",
                "denied",
            )
        if verdict.action == "ask":
            ap = await self.approvals.request(tool, args, ctx, verdict.reason, self.redactor)
            approval_id = ap["id"]
            if ctx.on_approval:
                ctx.on_approval(ap)
            self.store.add_activity(
                "approval", f"Waiting for your OK: {ap['title']}", verdict.reason, ctx.task_id, "notify"
            )
            state, new_args, note = await self.approvals.wait(approval_id)
            if state in ("rejected", "expired", "cancelled"):
                self._audit(
                    name,
                    args,
                    state if state != "cancelled" else "rejected",
                    ctx,
                    None,
                    note or "",
                    approval_id,
                )
                self.store.add_activity(
                    "approval",
                    f"{'Declined' if state == 'rejected' else state.capitalize()}: {ap['title']}",
                    note,
                    ctx.task_id,
                    "info",
                )
                why = {
                    "rejected": "The user rejected this action",
                    "expired": "The approval timed out, so the action was denied",
                    "cancelled": "The action was cancelled",
                }[state]
                return ToolOutcome(
                    False,
                    f"{why}{': ' + note if note else ''}. It was not performed. Do not retry unless asked.",
                    state,
                    approval_id=approval_id,
                )
            decision = state  # approved | edited
            if new_args:
                args = new_args

        try:
            value = await tool.func(args, ctx)
            ok = True
        except Exception as exc:  # tool errors go back to the model, not up the stack
            log.info("tool %s failed: %s", name, exc)
            value, ok = f"Error: {exc}", False
        text = truncate(self.redactor.text(to_text(value)), self.result_limit)
        self._audit(name, args, decision, ctx, ok, "" if ok else text, approval_id)
        if approval_id:
            self.store.update_approval(approval_id, result={"ok": ok, "text": text[:2000]})
        if "read" not in tool.tags or not ok:
            self.store.add_activity(
                "tool",
                f"{'Ran' if ok else 'Failed'}: {tool.card_title(args)}",
                None if ok else text[:300],
                ctx.task_id,
                "info" if ok else "warn",
            )
        return ToolOutcome(
            ok, text, decision if ok else "error", self.redactor.value(value) if ok else None, approval_id
        )

    def propose(self, name: str, args: dict[str, Any], ctx: ToolContext) -> asyncio.Task[ToolOutcome]:
        """Run `invoke` in the background: used for drafts (a brief's reply) that wait on a card."""
        return asyncio.create_task(self.invoke(name, args, ctx))

    async def check_external(
        self, name: str, tags: set[str], args: dict[str, Any], ctx: ToolContext, title: str | None = None
    ) -> ToolOutcome:
        """Gate a harness's own built-in tool (e.g. the Claude Agent SDK's Bash) without running it here."""

        async def _noop(a: dict[str, Any], c: ToolContext) -> str:
            return "allowed"

        shadow = Tool(
            name=name,
            description=f"{title or name} (harness built-in tool)",
            parameters={"type": "object"},
            func=_noop,
            tags=frozenset(tags),
            title=title,
        )
        self.tools.setdefault(name, shadow)
        try:
            return await self.invoke(name, args, ctx)
        finally:
            if self.tools.get(name) is shadow:
                del self.tools[name]


class GatedTools:
    """What a harness sees: the tool specs and one `invoke` that goes through the gate."""

    def __init__(self, registry: ToolRegistry, tools: list[Tool], ctx: ToolContext) -> None:
        self.registry = registry
        self.tools = {t.name: t for t in tools}
        self.ctx = ctx
        self.calls = 0

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self.tools.values()]

    def names(self) -> list[str]:
        return list(self.tools)

    async def invoke(self, name: str, args: dict[str, Any] | None, call_id: str | None = None) -> ToolOutcome:
        self.calls += 1
        if name not in self.tools:

            async def refuse() -> ToolOutcome:
                return ToolOutcome(False, f"Tool {name!r} is not available in this task.", "invalid")

            return await traced_call(name, args, self.ctx, call_id, refuse)
        return await self.registry.invoke(name, args, self.ctx, call_id)

    async def check_external(
        self, name: str, tags: set[str], args: dict[str, Any], title: str | None = None
    ) -> ToolOutcome:
        return await self.registry.check_external(name, tags, args, self.ctx, title)
