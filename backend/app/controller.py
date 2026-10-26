"""Runtime orchestration for live sessions: event loop, persistence, fan-out."""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from dataclasses import asdict
from typing import Any

from . import engine
from .bus import bus
from .db import db, dumps, loads, now
from .play_queue import PlayQueue
from .platform import build_adapter

# How many recent platform message IDs to remember per session for dedup.
DEDUP_WINDOW = 1000


class LiveController:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._metrics: dict[str, dict[str, int]] = {}
        self._seen: dict[str, deque[str]] = {}
        self._queues: dict[str, PlayQueue] = {}

    def is_running(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return bool(task and not task.done())

    async def start(
        self,
        title: str,
        event_rate: float,
        host_id: int,
        platform: str = "mock",
        room_id: str = "",
    ) -> dict[str, Any]:
        session_id = uuid.uuid4().hex[:12]
        db.execute(
            """
            INSERT INTO live_sessions (id, title, status, host_id, room_id, started_at, created_at)
            VALUES (?, ?, 'live', ?, ?, ?, ?)
            """,
            (session_id, title, host_id, room_id, now(), now()),
        )
        self._metrics[session_id] = {"online": 0, "peak_online": 0}
        self._seen[session_id] = deque(maxlen=DEDUP_WINDOW)
        queue = PlayQueue(on_change=lambda: self._broadcast_queue(session_id))
        self._queues[session_id] = queue
        await queue.start_auto_fill()
        adapter = build_adapter(platform, session_id, event_rate, room_id)
        task = asyncio.create_task(self._run(session_id, adapter))
        self._tasks[session_id] = task
        session = db.query_one("SELECT * FROM live_sessions WHERE id = ?", (session_id,))
        await bus.publish(session_id, {"type": "session", "data": {"status": "live", **session}})
        return session

    async def stop(self, session_id: str) -> dict[str, Any] | None:
        task = self._tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        session = db.query_one("SELECT * FROM live_sessions WHERE id = ?", (session_id,))
        if not session or session["status"] != "live":
            return session
        queue = self._queues.pop(session_id, None)
        if queue:
            await queue.stop_auto_fill()
            await queue.clear()
        metrics = self._metrics.get(session_id, {})
        db.execute(
            "UPDATE live_sessions SET status = 'ended', ended_at = ?, metrics = ? WHERE id = ?",
            (now(), dumps(metrics), session_id),
        )
        session = db.query_one("SELECT * FROM live_sessions WHERE id = ?", (session_id,))
        await bus.publish(session_id, {"type": "session", "data": {"status": "ended", **session}})
        return session

    async def push_approved(self, session_id: str, action: dict[str, Any]) -> None:
        """Queue an approved spoken action for the host to read out."""
        queue = self._queues.get(session_id)
        if not queue:
            return
        source = "welcome" if action["type"] == "welcome" else "danmaku"
        await queue.put_high(action["content"], source=source)

    async def pop_queue(self, session_id: str) -> dict[str, Any] | None:
        queue = self._queues.get(session_id)
        if not queue:
            return None
        item = await queue.pop_next()
        return asdict(item) if item else None

    def queue_snapshot(self, session_id: str) -> dict[str, Any]:
        queue = self._queues.get(session_id)
        return {
            "next": None,
            "high": [],
            "low": [],
        } if not queue else queue.snapshot_sync()

    def _broadcast_queue(self, session_id: str) -> None:
        asyncio.create_task(
            bus.publish(
                session_id,
                {"type": "queue", "data": self.queue_snapshot(session_id)},
            )
        )

    def snapshot(self, session_id: str) -> dict[str, Any]:
        events = db.query(
            "SELECT * FROM events WHERE session_id = ? ORDER BY id DESC LIMIT 30", (session_id,)
        )
        actions = db.query(
            "SELECT * FROM actions WHERE session_id = ? ORDER BY id DESC LIMIT 30", (session_id,)
        )
        for row in events:
            row["payload"] = loads(row.get("payload"), {})
        return {
            "metrics": self._metrics.get(session_id, {}),
            "recent_events": list(reversed(events)),
            "recent_actions": list(reversed(actions)),
        }

    def live_metrics(self, session_id: str) -> dict[str, int]:
        return self._metrics.get(session_id, {"online": 0, "peak_online": 0})

    async def _run(self, session_id: str, adapter) -> None:
        await adapter.connect()
        try:
            async for event in adapter.events():
                if self._is_duplicate(session_id, event):
                    continue
                result = await engine.process_event(event)
                if event.type == "chat":
                    event.payload["intent"] = result.intent
                    event.payload["sentiment"] = result.sentiment
                    event.payload["sensitive"] = result.sensitive
                    event.payload["kb_hit"] = result.kb_hit
                    event.payload["kb_tags"] = result.kb_tags

                event_id = db.execute(
                    "INSERT INTO events (session_id, type, payload, ts) VALUES (?, ?, ?, ?)",
                    (session_id, event.type, dumps(event.payload), event.ts),
                )
                event.id = event_id
                self._bump_online(session_id, event.type)
                await bus.publish(
                    session_id, {"type": "event", "data": self._event_out(event)}
                )

                for action in result.actions:
                    action.trigger_event_id = event_id
                    action_id = db.execute(
                        """
                        INSERT INTO actions (session_id, type, content, trigger_event_id, status, ts)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            action.type,
                            action.content,
                            event_id,
                            action.status,
                            action.ts,
                        ),
                    )
                    action.id = action_id
                    await bus.publish(
                        session_id, {"type": "action", "data": action.model_dump()}
                    )
        except asyncio.CancelledError:
            raise
        finally:
            await adapter.close()

    def _is_duplicate(self, session_id: str, event) -> bool:
        """Drop replays of the same platform message (e.g. after reconnect).

        Real adapters are expected to normalize the vendor message ID into
        ``payload['message_id']``; events without one always pass through.
        """
        message_id = str(event.payload.get("message_id", "")).strip()
        if not message_id:
            return False
        seen = self._seen.setdefault(session_id, deque(maxlen=DEDUP_WINDOW))
        if message_id in seen:
            return True
        seen.append(message_id)
        return False

    def _bump_online(self, session_id: str, event_type: str) -> None:
        metrics = self._metrics.setdefault(session_id, {"online": 0, "peak_online": 0})
        if event_type in {"user_enter", "follow"}:
            metrics["online"] += 1
            metrics["peak_online"] = max(metrics["peak_online"], metrics["online"])

    @staticmethod
    def _event_out(event) -> dict[str, Any]:
        return {
            "id": event.id,
            "session_id": event.session_id,
            "type": event.type,
            "payload": event.payload,
            "ts": event.ts,
        }


controller = LiveController()
