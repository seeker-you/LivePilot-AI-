"""Persisted moderation rules with validation and a short in-memory cache.

The moderation engine reads its thresholds and toggles from here instead of
hard-coded constants, so hosts/MCN admins can tune the AI without redeploying.
"""

from __future__ import annotations

import time
from typing import Any

from .db import db, dumps, loads, now


ALLOWED_ACTIONS = {"auto_reply", "welcome", "pin", "alert", "escalate"}

DEFAULTS: dict[str, Any] = {
    "gift_alert_threshold": 500,
    "gift_thank_threshold": 50,
    "vip_welcome_level": 40,
    "enabled_actions": ["auto_reply", "welcome", "pin", "alert", "escalate"],
    "sensitive_words": ["退款", "投诉", "315", "假货", "诈骗"],
}

_cache: dict[str, Any] | None = None
_cache_ts = 0.0
_TTL_SECONDS = 3.0


def get_rules(force: bool = False) -> dict[str, Any]:
    global _cache, _cache_ts
    now_ts = time.time()
    if _cache is not None and not force and now_ts - _cache_ts < _TTL_SECONDS:
        return _cache
    rules = dict(DEFAULTS)
    for row in db.query("SELECT key, value FROM settings"):
        rules[row["key"]] = loads(row["value"])
    rules["sensitive_words"] = [str(w) for w in rules.get("sensitive_words", [])]
    _cache = rules
    _cache_ts = now_ts
    return rules


def apply_rules(patch: dict[str, Any]) -> dict[str, Any]:
    """Validate a partial update and persist it, returning the full rule set."""
    current = get_rules(force=True)
    merged = dict(current)

    if "gift_alert_threshold" in patch:
        merged["gift_alert_threshold"] = _int_field(patch["gift_alert_threshold"], 0, 1_000_000)
    if "gift_thank_threshold" in patch:
        merged["gift_thank_threshold"] = _int_field(patch["gift_thank_threshold"], 0, 1_000_000)
    if "vip_welcome_level" in patch:
        merged["vip_welcome_level"] = _int_field(patch["vip_welcome_level"], 1, 200)
    if "enabled_actions" in patch:
        merged["enabled_actions"] = _actions_field(patch["enabled_actions"])
    if "sensitive_words" in patch:
        merged["sensitive_words"] = _words_field(patch["sensitive_words"])

    if merged["gift_thank_threshold"] > merged["gift_alert_threshold"]:
        raise ValueError("感谢阈值不能高于告警阈值")

    for key, value in merged.items():
        db.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, dumps(value), now()),
        )
    return get_rules(force=True)


def _int_field(value: Any, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError("阈值必须是整数") from None
    if not low <= number <= high:
        raise ValueError(f"阈值需在 {low} 到 {high} 之间")
    return number


def _actions_field(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("动作开关必须是列表")
    actions = [str(a) for a in value]
    unknown = set(actions) - ALLOWED_ACTIONS
    if unknown:
        raise ValueError(f"未知动作类型：{', '.join(sorted(unknown))}")
    return actions


def _words_field(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("敏感词必须是列表")
    words = []
    for item in value:
        word = str(item).strip()
        if not word:
            continue
        if len(word) > 30:
            raise ValueError("单个敏感词不能超过 30 字")
        words.append(word)
    return words

