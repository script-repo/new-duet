"""In-process pub/sub that feeds the SSE stream. Slow subscribers drop their oldest events."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


@dataclass
class BusEvent:
    type: str
    data: Any


class EventBus:
    def __init__(self, maxsize: int = 500) -> None:
        self._subs: set[asyncio.Queue[BusEvent]] = set()
        self._maxsize = maxsize
        self.history: list[BusEvent] = []  # last few events, handy in tests

    def publish(self, type_: str, data: Any) -> None:
        event = BusEvent(type_, data)
        self.history.append(event)
        del self.history[:-200]
        for q in list(self._subs):
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    def subscribe(self) -> asyncio.Queue[BusEvent]:
        q: asyncio.Queue[BusEvent] = asyncio.Queue(self._maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[BusEvent]) -> None:
        self._subs.discard(q)

    async def stream(self) -> AsyncIterator[BusEvent]:
        q = self.subscribe()
        try:
            while True:
                yield await q.get()
        finally:
            self.unsubscribe(q)

    def of_type(self, type_: str) -> list[Any]:
        return [e.data for e in self.history if e.type == type_]
