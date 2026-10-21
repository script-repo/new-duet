"""Approval policy (SPEC §8): which tool calls run, which ask the user, which are refused."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

Action = Literal["allow", "ask", "deny"]

DEFAULT_RULES: list[dict[str, Any]] = [
    {"match": {"tags": ["send"]}, "action": "ask", "reason": "Sends a message as you"},
    {"match": {"tags": ["pay"]}, "action": "ask", "reason": "Moves money"},
    {"match": {"tags": ["delete"]}, "action": "ask", "reason": "Deletes something"},
    {"match": {"tags": ["external_post"]}, "action": "ask", "reason": "Posts outside your workspace"},
    {
        "match": {"tags": ["exec"], "sandbox": "local"},
        "action": "ask",
        "reason": "Runs a command without container isolation",
    },
]


@dataclass
class Decision:
    action: Action
    reason: str = ""
    rule: int | None = None


@dataclass
class Rule:
    match: dict[str, Any]
    action: Action
    reason: str = ""

    def matches(
        self, tool: str, tags: set[str], args: dict[str, Any], sandbox: str | None, origin: str | None
    ) -> bool:
        m = self.match
        if "tool" in m and not any(fnmatch.fnmatchcase(tool, pat) for pat in _as_list(m["tool"])):
            return False
        if "tags" in m and not tags.intersection(_as_list(m["tags"])):
            return False
        if "sandbox" in m and sandbox not in _as_list(m["sandbox"]):
            return False
        if "origin" in m and origin not in _as_list(m["origin"]):
            return False
        return all(_cond(args.get(key), cond) for key, cond in (m.get("args") or {}).items())


def _as_list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else [v]


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


def _cond(value: Any, cond: Any) -> bool:
    if not isinstance(cond, dict):
        return value == cond
    for op, target in cond.items():
        if op == "eq" and value != target:
            return False
        if op == "ne" and value == target:
            return False
        if op in ("gt", "lt", "gte", "lte"):
            n = _num(value)
            if n is None:
                return False
            t = float(target)
            if (
                (op == "gt" and not n > t)
                or (op == "lt" and not n < t)
                or (op == "gte" and not n >= t)
                or (op == "lte" and not n <= t)
            ):
                return False
        if op == "in" and value not in target:
            return False
        if op == "regex" and not re.search(str(target), str(value or "")):
            return False
        if op == "contains" and str(target).lower() not in str(value or "").lower():
            return False
    return True


@dataclass
class Policy:
    default: Action = "allow"
    timeout_s: float = 900
    on_timeout: Literal["deny"] = "deny"
    rules: list[Rule] = field(default_factory=lambda: [Rule(**r) for r in DEFAULT_RULES])

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Policy:
        if data.get("on_timeout", "deny") != "deny":
            raise ValueError("policy.on_timeout must be 'deny': silence never approves an action")
        rules = data.get("rules")
        return cls(
            default=data.get("default", "allow"),
            timeout_s=float(data.get("timeout_s", 900)),
            rules=[
                Rule(match=r.get("match") or {}, action=r["action"], reason=r.get("reason", ""))
                for r in rules
            ]
            if rules is not None
            else [Rule(**r) for r in DEFAULT_RULES],
        )

    @classmethod
    def load(cls, path: Path | None) -> Policy:
        if path and Path(path).is_file():
            return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})
        return cls()

    def evaluate(
        self,
        tool: str,
        tags: set[str] | frozenset[str],
        args: dict[str, Any],
        sandbox: str | None = None,
        origin: str | None = None,
    ) -> Decision:
        for i, rule in enumerate(self.rules):
            if rule.matches(tool, set(tags), args, sandbox, origin):
                return Decision(rule.action, rule.reason, i)
        return Decision(self.default, "", None)

    def public(self) -> dict[str, Any]:
        return {
            "default": self.default,
            "timeout_s": self.timeout_s,
            "on_timeout": self.on_timeout,
            "rules": [{"match": r.match, "action": r.action, "reason": r.reason} for r in self.rules],
        }
