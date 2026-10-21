"""OpenAI Agents SDK harness (extra `[openai-agents]`).

Each gated Tandem tool becomes a `FunctionTool`. OpenAI and OpenAI-compatible profiles (NAI, vLLM,
Ollama, LiteLLM, Gemini's and Anthropic's OpenAI-compatible endpoints) run through
`OpenAIChatCompletionsModel` with an `AsyncOpenAI` client built from the profile; the key is resolved
server-side. Tracing to OpenAI is disabled unless `harness_options.openai-agents.tracing: true`.
"""

from __future__ import annotations

import json
from typing import Any

from tandem.harness.base import Harness, HarnessContext, HarnessResult, TaskSpec, register_harness, steer_text

COMPATIBLE = ("openai", "openai_compatible", "ollama")


def function_tools(ctx: HarnessContext, agents: Any) -> list[Any]:
    out = []
    for spec in ctx.tools.specs():

        def make(name: str) -> Any:
            async def on_invoke(run_ctx: Any, raw: str) -> str:
                try:
                    args = json.loads(raw or "{}")
                except json.JSONDecodeError:
                    args = {}
                await ctx.log("tool", name)
                outcome = await ctx.tools.invoke(name, args)
                await ctx.log("result", f"{name}: {outcome.decision} - {outcome.text[:300]}")
                return outcome.text

            return on_invoke

        out.append(
            agents.FunctionTool(
                name=spec.name,
                description=spec.description or spec.name,
                params_json_schema=spec.parameters,
                on_invoke_tool=make(spec.name),
                strict_json_schema=False,
            )
        )
    return out


@register_harness
class OpenAIAgentsHarness(Harness):
    name = "openai-agents"
    display_name = "OpenAI Agents SDK"
    description = "OpenAI's agent runner with function tools; works with any OpenAI-compatible endpoint."

    def availability(self) -> tuple[bool, str]:
        try:
            import agents  # noqa: F401
            import openai  # noqa: F401
        except ImportError:
            return False, "install the extra: uv sync --extra openai-agents"
        return True, "installed"

    async def run(self, task: TaskSpec, ctx: HarnessContext) -> HarnessResult:
        import agents
        from openai import AsyncOpenAI

        p = ctx.profile
        if p.provider not in COMPATIBLE:
            return HarnessResult(
                f"The OpenAI Agents SDK harness needs an OpenAI or OpenAI-compatible profile, not {p.provider}. "
                "Point a profile at your provider's OpenAI-compatible endpoint.",
                ok=False,
            )
        if not ctx.options.get("openai-agents", {}).get("tracing"):
            agents.set_tracing_disabled(True)
        key = (ctx.secrets.get(p.api_key_env) if ctx.secrets and p.api_key_env else None) or "not-needed"
        base = p.base_url or ("http://localhost:11434/v1" if p.provider == "ollama" else None)
        client = AsyncOpenAI(api_key=key, base_url=base)
        model = agents.OpenAIChatCompletionsModel(model=p.model, openai_client=client)
        agent = agents.Agent(
            name="Tandem worker",
            instructions=ctx.system_prompt,
            tools=function_tools(ctx, agents),
            model=model,
        )
        run_input: Any = task.instructions
        final = ""
        for _turn in range(5):
            result = await agents.Runner.run(agent, run_input, max_turns=ctx.max_steps)
            final = str(result.final_output or "")
            notes = ctx.drain_steer()
            if not notes:
                break
            for n in notes:
                await ctx.log("steer", n)
            run_input = [
                *result.to_input_list(),
                {"role": "user", "content": "\n".join(steer_text(n) for n in notes)},
            ]
        return HarnessResult(summary=final.strip() or "Done.")
