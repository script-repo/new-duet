"""Harness adapters: who executes a task (SPEC §6.3). Third-party harnesses register through the
`tandem.harnesses` entry point group (`name = "package.module:HarnessClass"`).
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points

from tandem.harness import builtin, claude_sdk, cli, deepagents_harness, openai_agents  # noqa: F401 - register
from tandem.harness.base import HARNESSES, Harness, HarnessContext, HarnessResult, TaskSpec, register_harness

log = logging.getLogger("tandem.harness")


def load_plugins() -> None:
    try:
        eps = entry_points(group="tandem.harnesses")
    except TypeError:  # pragma: no cover - very old importlib.metadata
        eps = []
    for ep in eps:
        try:
            cls = ep.load()
            HARNESSES[cls.name] = cls
        except Exception as exc:  # a broken plugin must not take the app down
            log.warning("could not load harness plugin %s: %s", ep.name, exc)


def get_harness(name: str) -> Harness:
    if name not in HARNESSES:
        raise KeyError(f"unknown harness {name!r}; known: {', '.join(sorted(HARNESSES))}")
    return HARNESSES[name]()


def describe(default: str) -> list[dict]:
    out = []
    for name, cls in HARNESSES.items():
        ok, reason = cls().availability()
        out.append(
            {
                "name": name,
                "display_name": cls.display_name,
                "description": cls.description,
                "available": ok,
                "reason": reason,
                "default": name == default,
            }
        )
    return out


__all__ = [
    "HARNESSES",
    "Harness",
    "HarnessContext",
    "HarnessResult",
    "TaskSpec",
    "describe",
    "get_harness",
    "load_plugins",
    "register_harness",
]
