"""The built-in harness: a small, dependable tool-calling loop on any `ModelClient`."""

from __future__ import annotations

from tandem.harness.base import Harness, HarnessContext, HarnessResult, TaskSpec, register_harness, steer_text
from tandem.models.base import Message


async def tool_loop(messages: list[Message], ctx: HarnessContext, max_steps: int) -> tuple[str, int]:
    """Run model ↔ tools until the model answers without tool calls. Returns (final text, steps)."""
    specs = ctx.tools.specs()
    for step in range(1, max_steps + 1):
        for note in ctx.drain_steer():
            messages.append(Message("user", steer_text(note)))
            await ctx.log("steer", note)
        resp = await ctx.model.complete(messages, specs)
        if resp.text.strip() and resp.tool_calls:
            await ctx.log("thought", resp.text.strip()[:2000])
        if not resp.tool_calls:
            return resp.text.strip(), step
        messages.append(Message("assistant", resp.text, tool_calls=resp.tool_calls))
        for call in resp.tool_calls:
            await ctx.log("tool", f"{call.name}({_brief_args(call.args)})")
            outcome = await ctx.tools.invoke(call.name, call.args)
            await ctx.log("result", f"{call.name}: {outcome.decision} - {outcome.text[:300]}")
            messages.append(Message("tool", outcome.text, tool_call_id=call.id, name=call.name))
    return "Stopped: reached the step limit before finishing.", max_steps


def _brief_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        s = str(v).replace("\n", " ")
        parts.append(f"{k}={s[:60] + '…' if len(s) > 60 else s}")
    return ", ".join(parts)


@register_harness
class BuiltinHarness(Harness):
    name = "builtin"
    display_name = "Built-in"
    description = "Tandem's own tool-calling loop. Works with every model provider, including mock mode."

    async def run(self, task: TaskSpec, ctx: HarnessContext) -> HarnessResult:
        messages = [Message("system", ctx.system_prompt), Message("user", task.instructions)]
        text, steps = await tool_loop(messages, ctx, ctx.max_steps)
        ctx.options["steps"] = steps
        return HarnessResult(summary=text or "Done.", ok=not text.startswith("Stopped:"))
