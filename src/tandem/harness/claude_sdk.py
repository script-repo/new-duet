"""Claude Agent SDK harness (extra `[claude-sdk]`; needs the Claude Code runtime the SDK drives).

Tandem's gated tools are served to the agent as an in-process SDK MCP server named `tandem`. The SDK's
own built-in tools (Bash, Write, Edit, ...) run with the sandbox directory as `cwd`, and every use of
one goes through `can_use_tool`, which asks Tandem's gate (policy, approval card, audit) first.
The model must be an Anthropic profile (or an Anthropic-compatible gateway via `base_url`); the key
is passed to the SDK's process environment, never into a prompt.
"""

from __future__ import annotations

from typing import Any

from tandem.harness.base import Harness, HarnessContext, HarnessResult, TaskSpec, register_harness, steer_text

# Built-in Claude Code tools and the tags Tandem's policy sees for them.
BUILTIN_TAGS: dict[str, set[str]] = {
    "Bash": {"exec"},
    "Write": {"write"},
    "Edit": {"write"},
    "MultiEdit": {"write"},
    "NotebookEdit": {"write"},
    "Read": {"read"},
    "Glob": {"read"},
    "Grep": {"read"},
    "LS": {"read"},
    "TodoWrite": {"read"},
    "WebFetch": {"read", "network"},
    "WebSearch": {"read", "network"},
}
ALLOWED_BUILTINS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash", "TodoWrite"]


def sdk_tools(ctx: HarnessContext, sdk: Any) -> list[Any]:
    out = []
    for spec in ctx.tools.specs():

        def make(name: str) -> Any:
            async def handler(args: dict[str, Any]) -> dict[str, Any]:
                await ctx.log("tool", name)
                outcome = await ctx.tools.invoke(name, args)
                await ctx.log("result", f"{name}: {outcome.decision} - {outcome.text[:300]}")
                return {"content": [{"type": "text", "text": outcome.text}], "is_error": not outcome.ok}

            return handler

        out.append(sdk.tool(spec.name, spec.description or spec.name, spec.parameters)(make(spec.name)))
    return out


@register_harness
class ClaudeSdkHarness(Harness):
    name = "claude-sdk"
    display_name = "Claude Agent SDK"
    description = "Anthropic's agent loop with Claude Code's file and shell tools, inside the sandbox."

    def availability(self) -> tuple[bool, str]:
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            return False, "install the extra: uv sync --extra claude-sdk (and the Claude Code runtime)"
        return True, "installed"

    async def run(self, task: TaskSpec, ctx: HarnessContext) -> HarnessResult:
        import claude_agent_sdk as sdk

        if ctx.profile.provider not in ("anthropic", "mock"):
            return HarnessResult(
                f"The Claude Agent SDK needs an Anthropic model profile, not {ctx.profile.provider}.",
                ok=False,
            )
        server = sdk.create_sdk_mcp_server(name="tandem", version="0.1.0", tools=sdk_tools(ctx, sdk))

        async def can_use_tool(tool_name: str, tool_input: dict[str, Any], context: Any) -> Any:
            if tool_name.startswith("mcp__tandem__"):
                return sdk.PermissionResultAllow()  # already gated inside the tool handler
            tags = BUILTIN_TAGS.get(tool_name, {"write"})
            outcome = await ctx.tools.check_external(
                f"claude_{tool_name.lower()}", tags, tool_input, title=f"Claude Code {tool_name}"
            )
            if outcome.ok:
                return sdk.PermissionResultAllow()
            return sdk.PermissionResultDeny(message=outcome.text)

        env: dict[str, str] = {}
        key = ctx.secrets.get(ctx.profile.api_key_env) if ctx.secrets and ctx.profile.api_key_env else None
        if key:
            env["ANTHROPIC_API_KEY"] = key
        if ctx.profile.base_url:
            env["ANTHROPIC_BASE_URL"] = ctx.profile.base_url
        options = sdk.ClaudeAgentOptions(
            system_prompt=ctx.system_prompt,
            mcp_servers={"tandem": server},
            allowed_tools=[f"mcp__tandem__{s.name}" for s in ctx.tools.specs()] + ALLOWED_BUILTINS,
            can_use_tool=can_use_tool,
            cwd=str(ctx.sandbox.root),
            model=ctx.profile.model if ctx.profile.provider == "anthropic" else None,
            max_turns=ctx.max_steps,
            env=env,
        )
        final: list[str] = []
        async with sdk.ClaudeSDKClient(options=options) as client:
            prompt = task.instructions
            for _turn in range(5):
                await client.query(prompt)
                async for message in client.receive_response():
                    for block in getattr(message, "content", None) or []:
                        text = getattr(block, "text", None)
                        if text:
                            final.append(text)
                            await ctx.log("thought", text[:1000])
                    result = getattr(message, "result", None)
                    if isinstance(result, str) and result:
                        final.append(result)
                notes = ctx.drain_steer()
                if not notes:
                    break
                for n in notes:
                    await ctx.log("steer", n)
                prompt = "\n".join(steer_text(n) for n in notes)
        return HarnessResult(summary=(final[-1] if final else "Done.").strip())
