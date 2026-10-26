"""Two-level priority speech queue: high for real-time replies, low for fillers.

Adapted from the LiveStream reference project. The high deque holds AI replies
for danmaku/gifts/welcomes (interrupting filler), while the low deque is
auto-filled from the script library so there is always a next line to speak.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from . import script_manager


@dataclass
class QueueItem:
    type: str = "text"
    content: str = ""
    source: str = "script"
    level: str = "low"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


class PlayQueue:
    def __init__(self, on_change: Callable[[], Any] | None = None) -> None:
        self._high: deque[QueueItem] = deque()
        self._low: deque[QueueItem] = deque()
        self._lock = asyncio.Lock()
        self._auto_fill_task: asyncio.Task | None = None
        self._running = False
        self._min_size = 2
        self._interval = 4.0
        self._on_change = on_change

    async def put_high(self, content: str, source: str = "danmaku") -> QueueItem:
        item = QueueItem(type="text", content=content, source=source, level="high")
        async with self._lock:
            self._high.append(item)
        await self._notify()
        return item

    async def put_low(self, item: QueueItem) -> QueueItem:
        item.level = "low"
        async with self._lock:
            self._low.append(item)
        await self._notify()
        return item

    async def pop_next(self) -> QueueItem | None:
        async with self._lock:
            if self._high:
                item = self._high.popleft()
            elif self._low:
                item = self._low.popleft()
            else:
                item = None
        if item:
            await self._notify()
        return item

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "next": self._peek(),
                "high": [asdict(i) for i in self._high],
                "low": [asdict(i) for i in self._low],
            }

    def snapshot_sync(self) -> dict[str, Any]:
        """Lock-free snapshot. Must be called from the asyncio event loop only."""
        return {
            "next": self._peek(),
            "high": [asdict(i) for i in self._high],
            "low": [asdict(i) for i in self._low],
        }

    def _peek(self) -> dict[str, Any] | None:
        item = self._high[0] if self._high else (self._low[0] if self._low else None)
        return asdict(item) if item else None

    async def clear(self) -> None:
        async with self._lock:
            self._high.clear()
            self._low.clear()
        await self._notify()

    async def start_auto_fill(self, min_size: int = 2, interval: float = 4.0) -> None:
        self._min_size = min_size
        self._interval = interval
        self._running = True
        self._auto_fill_task = asyncio.create_task(self._auto_fill_loop())

    async def stop_auto_fill(self) -> None:
        self._running = False
        if self._auto_fill_task:
            self._auto_fill_task.cancel()
            try:
                await self._auto_fill_task
            except asyncio.CancelledError:
                pass
            self._auto_fill_task = None

    async def _auto_fill_loop(self) -> None:
        while self._running:
            try:
                if self.low_size < self._min_size:
                    script = script_manager.weighted_pick()
                    if script:
                        await self.put_low(
                            QueueItem(
                                type=script["type"],
                                content=script["content"],
                                source="script",
                            )
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(self._interval)

    @property
    def low_size(self) -> int:
        return len(self._low)

    async def _notify(self) -> None:
        if self._on_change:
            try:
                result = self._on_change()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass
