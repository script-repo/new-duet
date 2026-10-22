"""Wire formats: Anthropic Messages, OpenAI Chat Completions (and every compatible server), Gemini.

Plain `httpx`, no vendor SDKs: small, version-proof, and testable with `httpx.MockTransport`.
API keys are read from the environment at call time and only ever go into request headers.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from tandem.models.base import (
    Message,
    ModelClient,
    ModelError,
    ModelResponse,
    ToolCall,
    ToolSpec,
    Usage,
    dumps_args,
    loads_args,
)

log = logging.getLogger("tandem.models")

# The largest max_tokens the out-of-tokens retry asks for.
OUT_OF_TOKENS_CAP = 64000

JSON_ONLY = "Respond with only a single JSON value. No prose, no code fences."


def _system_text(messages: list[Message], json_mode: bool) -> str:
    parts = [m.content for m in messages if m.role == "system" and m.content]
    if json_mode:
        parts.append(JSON_ONLY)
    return "\n\n".join(parts)


# ---- Anthropic ------------------------------------------------------------------------------------------


class AnthropicClient(ModelClient):
    default_base = "https://api.anthropic.com"

    def build_body(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        json_mode: bool,
        max_tokens: int,
        temperature: float | None,
    ) -> dict[str, Any]:
        out: list[dict[str, Any]] = []

        def push(role: str, block: dict[str, Any]) -> None:
            if out and out[-1]["role"] == role:
                out[-1]["content"].append(block)
            else:
                out.append({"role": role, "content": [block]})

        for m in messages:
            if m.role == "system":
                continue
            if m.role == "tool":
                push(
                    "user",
                    {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content or "(empty)"},
                )
            elif m.role == "assistant":
                if m.content:
                    push("assistant", {"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    push("assistant", {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.args})
                if not m.content and not m.tool_calls:
                    push("assistant", {"type": "text", "text": "(no content)"})
            else:
                push("user", {"type": "text", "text": m.content or "(empty)"})
        body: dict[str, Any] = {"model": self.profile.model, "max_tokens": max_tokens, "messages": out}
        system = _system_text(messages, json_mode and not tools)
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
            ]
        if temperature is not None:
            body["temperature"] = temperature
        return body

    async def _complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        json_mode: bool,
        max_tokens: int,
        temperature: float | None,
    ) -> ModelResponse:
        key = self.profile.api_key()
        if not key:
            raise ModelError(
                f"no API key: set {self.profile.api_key_env or 'api_key_env'} for model profile {self.profile.name!r}"
            )
        base = (self.profile.base_url or self.default_base).rstrip("/")
        url = f"{base}/v1/messages" if not base.endswith("/v1") else f"{base}/messages"
        async with self._client() as client:
            r = await client.post(
                url,
                json=self.body(
                    messages, tools, json_mode=json_mode, max_tokens=max_tokens, temperature=temperature
                ),
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            )
            r.raise_for_status()
            data = r.json()
        text, calls = [], []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(ToolCall(id=block["id"], name=block["name"], args=block.get("input") or {}))
        u = data.get("usage", {})
        return ModelResponse(
            "".join(text),
            calls,
            Usage(u.get("input_tokens", 0), u.get("output_tokens", 0)),
            data.get("model", self.profile.model),
            data.get("stop_reason"),
        )


# ---- OpenAI and OpenAI-compatible ---------------------------------------------------------------------------


class OpenAIClient(ModelClient):
    """OpenAI's API, or any server that speaks Chat Completions (vLLM, Ollama, LM Studio, LiteLLM, NAI...)."""

    def base_url(self) -> str:
        if self.profile.base_url:
            return self.profile.base_url.rstrip("/")
        if self.profile.provider == "ollama":
            return "http://localhost:11434/v1"
        if self.profile.provider == "openai":
            return "https://api.openai.com/v1"
        raise ModelError(
            f"model profile {self.profile.name!r}: base_url is required for provider openai_compatible"
        )

    def _max_tokens_key(self) -> str:
        return "max_completion_tokens" if self.profile.provider == "openai" else "max_tokens"

    def out_of_tokens_retry(self, body: dict[str, Any], max_tokens: int) -> dict[str, Any]:
        """The body for the one retry after an empty `finish_reason: length` answer."""
        retry = dict(body)
        retry[self._max_tokens_key()] = max(max_tokens, min(max_tokens * 4, OUT_OF_TOKENS_CAP))
        if not any(k in body for k in ("reasoning", "reasoning_effort")):
            if self.profile.provider == "openai":
                retry["reasoning_effort"] = "low"
            else:
                retry["reasoning"] = {"effort": "low"}  # OpenRouter's form; most other servers ignore it
        return retry

    async def _post(
        self, client: httpx.AsyncClient, url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        r = await client.post(url, json=body, headers=headers)
        r.raise_for_status()
        return r.json()

    def build_body(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        json_mode: bool,
        max_tokens: int,
        temperature: float | None,
    ) -> dict[str, Any]:
        out: list[dict[str, Any]] = []
        system = _system_text(messages, json_mode)
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            if m.role == "system":
                continue
            if m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
            elif m.role == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.content or None}
                if m.tool_calls:
                    msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": dumps_args(tc.args)},
                        }
                        for tc in m.tool_calls
                    ]
                out.append(msg)
            else:
                out.append({"role": "user", "content": m.content})
        body: dict[str, Any] = {"model": self.profile.model, "messages": out}
        # OpenAI's newer models take max_completion_tokens; compatible servers mostly expect max_tokens.
        body[self._max_tokens_key()] = max_tokens
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ]
        elif json_mode:
            body["response_format"] = {"type": "json_object"}
        if temperature is not None:
            body["temperature"] = temperature
        return body

    async def _complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        json_mode: bool,
        max_tokens: int,
        temperature: float | None,
    ) -> ModelResponse:
        key = self.profile.api_key()
        if self.profile.provider == "openai" and not key:
            raise ModelError(
                f"no API key: set {self.profile.api_key_env or 'api_key_env'} for model profile {self.profile.name!r}"
            )
        headers = {"Authorization": f"Bearer {key or 'not-needed'}"}
        url = f"{self.base_url()}/chat/completions"
        body = self.body(messages, tools, json_mode=json_mode, max_tokens=max_tokens, temperature=temperature)
        async with self._client() as client:
            data = await self._post(client, url, body, headers)
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            if _out_of_tokens(choice, msg):
                # A reasoning model spent the whole budget thinking and said nothing. A "not valid JSON"
                # repair prompt would fail the same way: ask once more with room to answer and less thinking.
                retry = self.out_of_tokens_retry(body, max_tokens)
                log.info(
                    "%s: empty answer at max_tokens=%s (finish_reason=length); retrying once with %s",
                    self.profile.model,
                    max_tokens,
                    retry[self._max_tokens_key()],
                )
                try:
                    data = await self._post(client, url, retry, headers)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 400 or "reasoning" not in retry or "reasoning" in body:
                        raise
                    retry.pop("reasoning")  # a server that rejects the field: just more tokens
                    data = await self._post(client, url, retry, headers)
                choice = (data.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
        calls = [
            ToolCall(
                id=tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                name=tc["function"]["name"],
                args=loads_args(tc["function"].get("arguments")),
            )
            for tc in msg.get("tool_calls") or []
        ]
        u = data.get("usage") or {}
        return ModelResponse(
            msg.get("content") or "",
            calls,
            Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0)),
            data.get("model", self.profile.model),
            choice.get("finish_reason"),
        )


def _out_of_tokens(choice: dict[str, Any], msg: dict[str, Any]) -> bool:
    return (
        choice.get("finish_reason") == "length"
        and not (msg.get("content") or "").strip()
        and not msg.get("tool_calls")
    )


# ---- Gemini -------------------------------------------------------------------------------------------------

_GEMINI_DROP = {"additionalProperties", "$schema", "default", "examples", "title"}


def gemini_schema(schema: Any) -> Any:
    """Gemini accepts an OpenAPI subset of JSON Schema; drop the keys it rejects."""
    if isinstance(schema, dict):
        return {k: gemini_schema(v) for k, v in schema.items() if k not in _GEMINI_DROP}
    if isinstance(schema, list):
        return [gemini_schema(v) for v in schema]
    return schema


class GeminiClient(ModelClient):
    default_base = "https://generativelanguage.googleapis.com/v1beta"

    def build_body(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        json_mode: bool,
        max_tokens: int,
        temperature: float | None,
    ) -> dict[str, Any]:
        contents: list[dict[str, Any]] = []

        def push(role: str, part: dict[str, Any]) -> None:
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": role, "parts": [part]})

        for m in messages:
            if m.role == "system":
                continue
            if m.role == "tool":
                push(
                    "user",
                    {"functionResponse": {"name": m.name or "tool", "response": {"content": m.content}}},
                )
            elif m.role == "assistant":
                if m.content:
                    push("model", {"text": m.content})
                for tc in m.tool_calls:
                    push("model", {"functionCall": {"name": tc.name, "args": tc.args}})
            else:
                push("user", {"text": m.content or "(empty)"})
        body: dict[str, Any] = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens}}
        system = _system_text(messages, False)
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": gemini_schema(t.parameters),
                        }
                        for t in tools
                    ]
                }
            ]
        elif json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        if temperature is not None:
            body["generationConfig"]["temperature"] = temperature
        return body

    async def _complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        json_mode: bool,
        max_tokens: int,
        temperature: float | None,
    ) -> ModelResponse:
        key = self.profile.api_key()
        if not key:
            raise ModelError(
                f"no API key: set {self.profile.api_key_env or 'api_key_env'} for model profile {self.profile.name!r}"
            )
        base = (self.profile.base_url or self.default_base).rstrip("/")
        async with self._client() as client:
            r = await client.post(
                f"{base}/models/{self.profile.model}:generateContent",
                json=self.body(
                    messages, tools, json_mode=json_mode, max_tokens=max_tokens, temperature=temperature
                ),
                headers={"x-goog-api-key": key},
            )
            r.raise_for_status()
            data = r.json()
        cand = (data.get("candidates") or [{}])[0]
        text, calls = [], []
        for part in (cand.get("content") or {}).get("parts", []):
            if "text" in part:
                text.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                calls.append(
                    ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=fc["name"], args=fc.get("args") or {})
                )
        u = data.get("usageMetadata") or {}
        return ModelResponse(
            "".join(text),
            calls,
            Usage(u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0)),
            self.profile.model,
            cand.get("finishReason"),
        )
