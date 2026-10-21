"""System prompts. Kept short: the model gets the user's core memory, skills and the rules of the house.

The text lives in `src/tandem/prompts/*.md` (one file per prompt, `str.format` placeholders), so that the
harness manifest (`harness.yaml`) points at the real prompts and a quality tool such as ambientq can grade
them and propose line-level fixes.
"""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").rstrip("\n")


PERSONA = _load("persona")
RULES = _load("rules")
WORKER = _load("worker")
CHAT = _load("chat")
TRIAGE = _load("triage")
BRIEF = _load("brief")
