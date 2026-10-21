"""Small shared helpers: ids, clocks, JSON extraction."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    # Fixed width (always microseconds) so ISO strings sort correctly as text.
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_iso(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class Clock:
    """Wall clock. Tests use `FakeClock` to play a whole day in milliseconds."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock(Clock):
    def __init__(self, start: datetime) -> None:
        self._now = start if start.tzinfo else start.replace(tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def set(self, when: datetime) -> None:
        self._now = when if when.tzinfo else when.replace(tzinfo=UTC)

    def advance(self, **kwargs: float) -> datetime:
        self._now += timedelta(**kwargs)
        return self._now


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Parse JSON from a model's text: a fenced block, the whole text, or the first {...} / [...]."""
    text = text.strip()
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except (json.JSONDecodeError, ValueError):
            continue
    raise ValueError("no JSON object found in model output")


def truncate(text: str, limit: int = 8000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 40] + f"\n... [truncated {len(text) - limit + 40} chars]"


def to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str, indent=1)
    except (TypeError, ValueError):
        return str(value)
