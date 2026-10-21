"""Models: one interface, many providers, chosen per role (SPEC §6.1)."""

from __future__ import annotations

import httpx

from tandem.config import ROLES, AppConfig, ModelProfile
from tandem.models.base import Message, ModelClient, ModelError, ModelResponse, ToolCall, ToolSpec, Usage
from tandem.models.mock import MockModel
from tandem.models.providers import AnthropicClient, GeminiClient, OpenAIClient

PROVIDERS = ("mock", "anthropic", "openai", "openai_compatible", "gemini", "ollama")


def build_client(
    profile: ModelProfile, role: str = "worker", transport: httpx.AsyncBaseTransport | None = None
) -> ModelClient:
    if profile.provider == "mock":
        return MockModel(profile, role)
    if profile.provider == "anthropic":
        return AnthropicClient(profile, role, transport)
    if profile.provider in ("openai", "openai_compatible", "ollama"):
        return OpenAIClient(profile, role, transport)
    if profile.provider == "gemini":
        return GeminiClient(profile, role, transport)
    raise ValueError(f"unknown provider {profile.provider!r}; expected one of {', '.join(PROVIDERS)}")


class ModelRouter:
    """Maps roles (triage, brief, chat, worker) to clients, so check-ins can use a cheap model."""

    def __init__(self, cfg: AppConfig, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.cfg = cfg
        self.transport = transport
        self._clients: dict[str, ModelClient] = {}

    def profile(self, role: str) -> ModelProfile:
        return self.cfg.profile_for(role)

    def for_role(self, role: str) -> ModelClient:
        if role not in self._clients:
            self._clients[role] = build_client(self.profile(role), role, self.transport)
        return self._clients[role]

    def public(self) -> dict[str, dict]:
        return {role: self.profile(role).public() for role in ROLES}


__all__ = [
    "PROVIDERS",
    "Message",
    "MockModel",
    "ModelClient",
    "ModelError",
    "ModelResponse",
    "ModelRouter",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "build_client",
]
