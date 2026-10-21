"""Memory and skills (SPEC §3.11).

Two tiers, after Letta/MemGPT: a small *core* block (who the user is, how they like things) that goes
into every prompt, and a searchable *archive* (facts, notes, thread summaries). Skills are markdown
files with `name`/`description` front matter; their index is in every worker prompt and the body is
loaded on demand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tandem.store import Store

CORE_KINDS = ("core", "preference")
ARCHIVE_KINDS = ("fact", "note", "thread_summary")
KINDS = CORE_KINDS + ARCHIVE_KINDS
_WORD = re.compile(r"[a-z0-9@._-]{2,}")


def _terms(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


class Memory:
    def __init__(self, store: Store, core_chars: int = 2000) -> None:
        self.store = store
        self.core_chars = core_chars

    def write(
        self, content: str, kind: str = "fact", key: str | None = None, source: str = "agent"
    ) -> dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        content = content.strip()
        if not content:
            raise ValueError("content is empty")
        return self.store.write_memory(kind, content[:4000], key, source)

    def forget(self, memory_id: str) -> bool:
        return self.store.delete_memory(memory_id)

    def all(self) -> list[dict[str, Any]]:
        return self.store.list_memory()

    def core_block(self) -> str:
        lines = []
        used = 0
        for m in self.store.list_memory(list(CORE_KINDS)):
            line = f"- ({m['kind']}) {m['content']}"
            if used + len(line) > self.core_chars:
                break
            lines.append(line)
            used += len(line)
        return "\n".join(lines) if lines else "(nothing yet)"

    def preferences(self) -> list[str]:
        return [m["content"] for m in self.store.list_memory(["preference"])]

    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        q = _terms(query)
        scored = []
        for m in self.store.list_memory():
            t = _terms(m["content"] + " " + (m.get("key") or ""))
            score = len(q & t) / (len(q) or 1)
            if score > 0 or not q:
                scored.append((score, m["updated_at"], m))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [m for _, _, m in scored[:k]]

    def rules(self, prefix: str) -> list[str]:
        """Values of preferences written as `prefix: value`, e.g. `vip: elena@harbor.vc`."""
        out = []
        for p in self.preferences():
            for part in re.split(r"[;\n]", p):
                m = re.match(rf"\s*{prefix}\s*[:=]\s*(.+)", part, re.I)
                if m:
                    out.extend(v.strip().lower() for v in m.group(1).split(",") if v.strip())
        return out


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path


def _parse_skill(path: Path) -> Skill | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    meta: dict[str, Any] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                meta = {}
            body = parts[2].lstrip()
    name = str(meta.get("name") or (path.parent.name if path.name.upper() == "SKILL.MD" else path.stem))
    first_line = body.strip().splitlines()[0][:160] if body.strip() else ""
    desc = str(meta.get("description") or first_line)
    return Skill(name=name, description=desc, body=body, path=path)


class Skills:
    def __init__(self, folders: list[Path]) -> None:
        self.folders = folders

    def all(self) -> dict[str, Skill]:
        found: dict[str, Skill] = {}
        for folder in self.folders:
            if not folder.is_dir():
                continue
            for path in sorted([*folder.glob("*/SKILL.md"), *folder.glob("*.md")]):
                if path.name.lower() == "readme.md":
                    continue
                skill = _parse_skill(path)
                if skill:
                    found[skill.name] = skill
        return found

    def index(self) -> str:
        skills = self.all()
        if not skills:
            return "(no skills installed)"
        return "\n".join(f"- {s.name}: {s.description}" for s in skills.values())

    def load(self, name: str) -> str:
        skills = self.all()
        if name not in skills:
            raise KeyError(f"no skill named {name!r}; available: {', '.join(skills) or 'none'}")
        return skills[name].body

    def public(self) -> list[dict[str, str]]:
        return [{"name": s.name, "description": s.description} for s in self.all().values()]
