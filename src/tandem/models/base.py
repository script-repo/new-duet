"""Provider-neutral chat types and the `ModelClient` interface (SPEC §6.1)."""

from __future__ import annotations

import asyncio
import json
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from tandem.config import ModelProfile
from tandem.telemetry import KIND_CLIENT, PROVIDER_NAMES, tracer
from tandem.util import extract_json


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None  # tool name, for tool results


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ModelResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    stop_reason: str | None = None


class ModelError(RuntimeError):
    pass


RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


class ModelClient(ABC):
    """One configured model. Subclasses implement `_complete`; retries and JSON parsing live here."""

    def __init__(
        self,
        profile: ModelProfile,
        role: str = "worker",
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 3,
    ) -> None:
        self.profile = profile
        self.role = role
        self.max_retries = max_retries
        self._transport = transport
        self.usage = Usage()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.profile.timeout_s, transport=self._transport)

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ModelResponse:
        t = tracer()
        attrs = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": PROVIDER_NAMES.get(self.profile.provider, self.profile.provider),
            "gen_ai.request.model": self.profile.model,
            "gen_ai.request.max_tokens": max_tokens or self.profile.max_tokens,
            "gen_ai.request.temperature": temperature
            if temperature is not None
            else self.profile.temperature,
            "gen_ai.output.type": "json" if json_mode else "text",
            "server.address": self.profile.base_url or None,
            "tandem.role": self.role,
        }
        with t.span(f"chat {self.profile.model}", attrs, kind=KIND_CLIENT) as sp:
            resp = None
            try:
                resp = await self._complete_retrying(
                    messages, tools, json_mode=json_mode, max_tokens=max_tokens, temperature=temperature
                )
                return resp
            finally:
                t.record_model_call(sp, messages, resp, tools)

    async def _complete_retrying(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        *,
        json_mode: bool,
        max_tokens: int | None,
        temperature: float | None,
    ) -> ModelResponse:
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self._complete(
                    messages,
                    tools or [],
                    json_mode=json_mode,
                    max_tokens=max_tokens or self.profile.max_tokens,
                    temperature=temperature if temperature is not None else self.profile.temperature,
                )
                self.usage.input_tokens += resp.usage.input_tokens
                self.usage.output_tokens += resp.usage.output_tokens
                return resp
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in RETRY_STATUS or attempt == self.max_retries:
                    raise ModelError(
                        f"{self.profile.provider} {exc.response.status_code}: {exc.response.text[:500]}"
                    ) from exc
                retry_after = exc.response.headers.get("retry-after")
                wait = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else delay
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt == self.max_retries:
                    raise ModelError(f"{self.profile.provider}: {exc}") from exc
                wait = delay
            await asyncio.sleep(min(wait, 30) * (0.5 + random.random()))
            delay *= 2
        raise ModelError("unreachable")

    def body(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """The provider request body plus the profile's `extra_body` (e.g. reasoning effort, vLLM kwargs)."""
        return {**self.build_body(*args, **kwargs), **(self.profile.extra_body or {})}

    def build_body(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    async def complete_json(self, messages: list[Message], **kwargs: Any) -> Any:
        """Ask for JSON and parse it; one repair round if the first answer is not valid JSON."""
        resp = await self.complete(messages, json_mode=True, **kwargs)
        try:
            return extract_json(resp.text)
        except ValueError:
            if resp.stop_reason in ("length", "max_tokens", "MAX_TOKENS") and not resp.text.strip():
                raise ValueError(
                    f"{self.profile.model} used all {self.profile.max_tokens} max_tokens before answering "
                    "(a reasoning model? raise max_tokens or lower its reasoning effort with extra_body)"
                ) from None
            repair = [
                *messages,
                Message("assistant", resp.text),
                Message("user", "That was not valid JSON. Reply with only the JSON object, no prose."),
            ]
            resp = await self.complete(repair, json_mode=True, **kwargs)
            return extract_json(resp.text)

    @abstractmethod
    async def _complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        json_mode: bool,
        max_tokens: int,
        temperature: float | None,
    ) -> ModelResponse: ...


def dumps_args(args: dict[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False)


def loads_args(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {"value": val}
    except json.JSONDecodeError:
        return {"_raw": raw}
