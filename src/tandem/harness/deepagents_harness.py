"""LangChain Deep Agents harness (extra `[deepagents]`).

By default the agent's model is a thin LangChain wrapper around Tandem's own `ModelClient`, so every
provider Tandem supports (mock included) works here too, with the same key handling. Set
`harness_options.deepagents.native_model: true` to use LangChain's `init_chat_model` instead.
MCP servers listed in `harness_options.deepagents.mcp_servers` are loaded with `langchain-mcp-adapters`.
"""

from __future__ import annotations

import inspect
from typing import Any

from tandem.harness.base import Harness, HarnessContext, HarnessResult, TaskSpec, register_harness, steer_text
from tandem.models.base import Message, ModelClient, ToolCall, ToolSpec


def langchain_bridge(client: ModelClient) -> Any:
    """A LangChain `BaseChatModel` that delegates to a Tandem `ModelClient`."""
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.utils.function_calling import convert_to_openai_tool

    class TandemChatModel(BaseChatModel):
        tandem_client: Any = None
        tandem_tools: list[Any] = []

        @property
        def _llm_type(self) -> str:
            return "tandem"

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            specs = []
            for t in tools:
                fn = convert_to_openai_tool(t)["function"]
                specs.append(
                    ToolSpec(
                        fn["name"], fn.get("description", ""), fn.get("parameters") or {"type": "object"}
                    )
                )
            return self.model_copy(update={"tandem_tools": specs})

        @staticmethod
        def _convert(messages: list[BaseMessage]) -> list[Message]:
            out = []
            for m in messages:
                content = (
                    m.content
                    if isinstance(m.content, str)
                    else "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in m.content)
                )
                if m.type == "system":
                    out.append(Message("system", content))
                elif m.type == "human":
                    out.append(Message("user", content))
                elif m.type == "ai":
                    calls = [
                        ToolCall(tc.get("id") or "", tc["name"], tc.get("args") or {})
                        for tc in getattr(m, "tool_calls", [])
                    ]
                    out.append(Message("assistant", content, tool_calls=calls))
                elif isinstance(m, ToolMessage):
                    out.append(Message("tool", content, tool_call_id=m.tool_call_id, name=m.name))
            return out

        async def _agenerate(
            self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
        ) -> Any:
            resp = await self.tandem_client.complete(self._convert(messages), self.tandem_tools or None)
            msg = AIMessage(
                content=resp.text,
                tool_calls=[
                    {"name": c.name, "args": c.args, "id": c.id, "type": "tool_call"} for c in resp.tool_calls
                ],
            )
            return ChatResult(generations=[ChatGeneration(message=msg)])

        def _generate(
            self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
        ) -> Any:
            raise NotImplementedError("Tandem's bridge is async-only; use ainvoke")

    return TandemChatModel(tandem_client=client)


def native_model(ctx: HarnessContext) -> Any:
    from langchain.chat_models import init_chat_model

    p = ctx.profile
    provider = {"openai_compatible": "openai", "ollama": "openai", "gemini": "google_genai"}.get(
        p.provider, p.provider
    )
    kwargs: dict[str, Any] = {"model_provider": provider}
    key = ctx.secrets.get(p.api_key_env) if ctx.secrets and p.api_key_env else None
    if key:
        kwargs["api_key"] = key
    if p.base_url:
        kwargs["base_url"] = p.base_url
    return init_chat_model(p.model, **kwargs)


def wrap_tools(ctx: HarnessContext) -> list[Any]:
    """Each gated Tandem tool as a LangChain StructuredTool (approval and audit happen inside invoke)."""
    from langchain_core.tools import StructuredTool

    def make(name: str) -> Any:
        async def call(**kwargs: Any) -> str:
            await ctx.log("tool", f"{name}({', '.join(f'{k}={str(v)[:40]}' for k, v in kwargs.items())})")
            outcome = await ctx.tools.invoke(name, kwargs)
            await ctx.log("result", f"{name}: {outcome.decision} - {outcome.text[:300]}")
            return outcome.text

        return call

    return [
        StructuredTool.from_function(
            coroutine=make(s.name), name=s.name, description=s.description or s.name, args_schema=s.parameters
        )
        for s in ctx.tools.specs()
    ]


@register_harness
class DeepAgentsHarness(Harness):
    name = "deepagents"
    display_name = "LangChain Deep Agents"
    description = "Planning, a virtual file system and sub-agents from langchain-ai/deepagents."

    def availability(self) -> tuple[bool, str]:
        try:
            import deepagents  # noqa: F401
            import langchain_core  # noqa: F401
        except ImportError:
            return False, "install the extra: uv sync --extra deepagents"
        return True, "installed"

    async def run(self, task: TaskSpec, ctx: HarnessContext) -> HarnessResult:
        from deepagents import create_deep_agent

        opts = ctx.options.get("deepagents", {})
        model = native_model(ctx) if opts.get("native_model") else langchain_bridge(ctx.model)
        tools = wrap_tools(ctx)
        if opts.get("mcp_servers"):
            from langchain_mcp_adapters.client import MultiServerMCPClient

            tools += await MultiServerMCPClient(opts["mcp_servers"]).get_tools()
        params = inspect.signature(create_deep_agent).parameters
        prompt_kw = "system_prompt" if "system_prompt" in params else "instructions"
        agent = create_deep_agent(model=model, tools=tools, **{prompt_kw: ctx.system_prompt})
        await ctx.log("info", "Deep Agents graph ready")
        messages: list[Any] = [{"role": "user", "content": task.instructions}]
        final = ""
        for _turn in range(5):
            result = await agent.ainvoke(
                {"messages": messages}, config={"recursion_limit": ctx.max_steps * 4}
            )
            messages = list(result["messages"])
            last = messages[-1]
            final = last.content if isinstance(last.content, str) else str(last.content)
            notes = ctx.drain_steer()
            if not notes:
                break
            for n in notes:
                await ctx.log("steer", n)
            messages.append({"role": "user", "content": "\n".join(steer_text(n) for n in notes)})
        return HarnessResult(summary=final.strip() or "Done.")
