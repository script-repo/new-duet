"""The harness contract (SPEC §6.3). A harness executes one task with gated tools in a sandbox."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from tandem.config import ModelProfile
from tandem.models.base import ModelClient
from tandem.sandbox import Sandbox
from tandem.tools import GatedTools


@dataclass
class TaskSpec:
    id: str
    title: str
    instructions: str
    harness: str
    origin: str = "user"


@dataclass
class HarnessContext:
    model: ModelClient
    profile: ModelProfile
    tools: GatedTools
    sandbox: Sandbox
    system_prompt: str
    log: Callable[[str, str], Awaitable[None]]
    steer: asyncio.Queue[str]
    max_steps: int = 24
    secrets: Any = None
    options: dict[str, Any] = field(default_factory=dict)

    def drain_steer(self) -> list[str]:
        out = []
        while not self.steer.empty():
            out.append(self.steer.get_nowait())
        return out


@dataclass
class HarnessResult:
    summary: str
    ok: bool = True
    artifacts: list[str] = field(default_factory=list)


class Harness(ABC):
    name: ClassVar[str]
    display_name: ClassVar[str]
    description: ClassVar[str] = ""

    def availability(self) -> tuple[bool, str]:
        return True, "built in"

    @abstractmethod
    async def run(self, task: TaskSpec, ctx: HarnessContext) -> HarnessResult: ...


HARNESSES: dict[str, type[Harness]] = {}


def register_harness(cls: type[Harness]) -> type[Harness]:
    HARNESSES[cls.name] = cls
    return cls


def steer_text(note: str) -> str:
    return f"[Steer from the user] {note}"
