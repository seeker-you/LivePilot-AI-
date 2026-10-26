"""In-memory pub/sub used to fan live events out to WebSocket clients."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, session_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        async with self._lock:
            self._subscribers[session_id].add(queue)
        return queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers[session_id].discard(queue)
            if not self._subscribers[session_id]:
                self._subscribers.pop(session_id, None)

    async def publish(self, session_id: str, message: dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(session_id, ()))
        for queue in queues:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # Drop for slow consumers instead of blocking the producer.
                continue


bus = EventBus()

