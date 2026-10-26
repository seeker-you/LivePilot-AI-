"""Script library management and weighted selection.

Mirrors the LiveStream reference project's idea: enabled scripts fill the
low-priority speech queue, with per-script play counts biasing the pick toward
under-used lines and a short recent-pick list avoiding immediate repetition.
"""

from __future__ import annotations

import random
from typing import Any

from .db import db, dumps, loads, now


def list_scripts() -> list[dict[str, Any]]:
    rows = db.query("SELECT * FROM scripts ORDER BY created_at DESC")
    for row in rows:
        row["tags"] = loads(row.get("tags"), [])
        row["enabled"] = bool(row.get("enabled"))
    return rows


def get_script(script_id: int) -> dict[str, Any] | None:
    row = db.query_one("SELECT * FROM scripts WHERE id = ?", (script_id,))
    if row:
        row["tags"] = loads(row.get("tags"), [])
        row["enabled"] = bool(row.get("enabled"))
    return row


def create_script(title: str, content: str, type_: str, tags: list[str]) -> dict[str, Any]:
    script_id = db.execute(
        """
        INSERT INTO scripts (title, content, type, tags, enabled, play_count, created_at)
        VALUES (?, ?, ?, ?, 1, 0, ?)
        """,
        (title, content, type_, dumps(tags), now()),
    )
    return get_script(script_id) or {}


def update_script(script_id: int, patch: dict[str, Any]) -> dict[str, Any] | None:
    current = get_script(script_id)
    if not current:
        return None
    title = patch.get("title", current["title"])
    content = patch.get("content", current["content"])
    type_ = patch.get("type", current["type"])
    tags = patch.get("tags", current["tags"])
    enabled = patch.get("enabled", current["enabled"])
    db.execute(
        """
        UPDATE scripts SET title = ?, content = ?, type = ?, tags = ?, enabled = ?
        WHERE id = ?
        """,
        (title, content, type_, dumps(tags), int(bool(enabled)), script_id),
    )
    return get_script(script_id)


def delete_script(script_id: int) -> bool:
    return db.execute("DELETE FROM scripts WHERE id = ?", (script_id,)) > 0


# Module-level recent-pick history. Bounded to avoid endless growth; it is a
# heuristic, not a correctness-critical structure.
_recent: list[int] = []
_MAX_RECENT = 6


def weighted_pick() -> dict[str, Any] | None:
    """Pick an enabled script, biased toward under-played lines, avoiding
    the most recent picks when alternatives exist."""
    rows = [r for r in list_scripts() if r["enabled"]]
    if not rows:
        return None

    candidates = [r for r in rows if r["id"] not in _recent] or rows
    if not candidates:
        return None

    max_play = max((r["play_count"] for r in candidates), default=0) + 2
    weights = [max_play - r["play_count"] for r in candidates]
    chosen = random.choices(candidates, weights=weights, k=1)[0]

    db.execute(
        "UPDATE scripts SET play_count = play_count + 1, last_used_at = ? WHERE id = ?",
        (now(), chosen["id"]),
    )
    _recent.append(chosen["id"])
    if len(_recent) > _MAX_RECENT:
        _recent.pop(0)

    chosen["play_count"] += 1
    return chosen


def seed_default_scripts() -> None:
    """Starter lines so the speech queue has filler out of the box."""
    if list_scripts():
        return
    defaults = [
        ("欢迎新朋友", "欢迎新进来的宝宝，先点个关注，今晚福利不间断～"),
        ("主推商品预告", "今晚主推的这款熬夜修护面霜，成分扎实、肤感清爽，等会儿给大家讲透。"),
        ("优惠提醒", "直播间专属价还买一送一，前 100 名加赠小样，看中的宝宝直接拍。"),
        ("互动引导", "有问题直接扣在弹幕上，主播看到都会解答，别害羞～"),
    ]
    for title, content in defaults:
        create_script(title, content, "text", ["话术", "通用"])

