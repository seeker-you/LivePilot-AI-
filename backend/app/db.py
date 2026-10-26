"""Small thread-safe SQLite repository used by the whole backend.

SQLite keeps the demo runnable without extra infrastructure while the schema
mirrors what a Postgres deployment would look like. Swap ``DATABASE_PATH``
for a Postgres DSN in a real rollout.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Iterable

from .config import settings


class Database:
    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'operator',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            product TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            content TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS knowledge_embeddings (
            knowledge_id INTEGER PRIMARY KEY,
            model TEXT NOT NULL,
            dim INTEGER NOT NULL,
            vector TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'text',
            tags TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1,
            play_count INTEGER NOT NULL DEFAULT 0,
            last_used_at REAL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS live_sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'idle',
            host_id INTEGER NOT NULL,
            started_at REAL,
            ended_at REAL,
            metrics TEXT,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            type TEXT NOT NULL,
            payload TEXT NOT NULL,
            ts REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            type TEXT NOT NULL,
            content TEXT NOT NULL,
            trigger_event_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewed_by INTEGER,
            ts REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_actions_session ON actions(session_id, id);
        CREATE TABLE IF NOT EXISTS idempotency (
            key TEXT PRIMARY KEY,
            request_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'processing',
            response_code INTEGER,
            response_body TEXT,
            created_at REAL NOT NULL
        );
        """
        with self._lock:
            self._conn.executescript(schema)
            self._conn.commit()
        self._ensure_column("live_sessions", "metrics", "metrics TEXT")
        self._ensure_column("live_sessions", "room_id", "room_id TEXT NOT NULL DEFAULT ''")

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        """Lightweight idempotent migration for databases created by an older
        schema. Keeps the demo DB upgradeable without a full migration tool."""
        with self._lock:
            columns = {
                row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            if column not in columns:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
                self._conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur.lastrowid or 0

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()


db = Database(settings.database_url)


def now() -> float:
    return time.time()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
