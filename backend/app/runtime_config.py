"""Runtime-mutable application configuration (admin-manageable).

Values live in the ``settings`` table and override env/defaults from
``config.Settings`` on read. Used for the LLM API config so admins can wire
credentials through the UI instead of environment plumbing.
"""

from __future__ import annotations

from .config import settings
from .db import db, now

# Keys managed through the admin UI. DB rows take priority over env defaults.
LLM_SETTING_KEYS = (
    "llm_api_key",
    "llm_base_url",
    "llm_model",
    "llm_embedding_model",
    "llm_embedding_base_url",
    "llm_embedding_api_key",
)

# Values that must never be echoed back verbatim to the client.
SECRET_KEYS = {"llm_api_key", "llm_embedding_api_key"}

# Knowledge retrieval knobs, managed through the admin UI. DB rows take
# priority over env defaults. Stored as strings, parsed to int on read.
RETRIEVAL_KEYS = ("chunk_size", "chunk_overlap", "top_k")

# AI persona (style) keys — drive the system-prompt prefix for text generation.
PERSONA_KEYS = (
    "persona_name",
    "persona_personality",
    "persona_style",
    "persona_knowledge_scope",
    "persona_forbidden_topics",
)
PERSONA_DEFAULTS = {
    "persona_name": "小鹿",
    "persona_personality": "热情友好、专业可信、有耐心",
    "persona_style": "亲切自然、口语化，善用直播间互动话术",
    "persona_knowledge_scope": "美妆护肤知识与直播间商品卖点",
    "persona_forbidden_topics": "政治、医疗诊断、绝对化疗效承诺",
}

_cache: dict[str, str] = {}


def _set_overrides(keys: tuple[str, ...], values: dict) -> None:
    for key in keys:
        if key not in values:
            continue
        value = values[key]
        if value is None:
            continue
        db.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, value, now()),
        )
        if value:
            _cache[key] = value
        else:
            _cache.pop(key, None)


def get_llm_config() -> dict[str, str]:
    """Effective LLM config: admin-set DB values over env/defaults."""
    cfg = {
        "llm_api_key": settings.llm_api_key,
        "llm_base_url": settings.llm_base_url,
        "llm_model": settings.llm_model,
        "llm_embedding_model": settings.llm_embedding_model,
        "llm_embedding_base_url": settings.llm_embedding_base_url,
        "llm_embedding_api_key": settings.llm_embedding_api_key,
    }
    if _cache:
        cfg.update(_cache)
    return cfg


def get_embedding_config() -> dict[str, str]:
    """向量接口：独立 base_url / key 优先，留空则复用对话配置。"""
    cfg = get_llm_config()
    return {
        "base_url": cfg["llm_embedding_base_url"] or cfg["llm_base_url"],
        "api_key": cfg["llm_embedding_api_key"] or cfg["llm_api_key"],
        "model": cfg["llm_embedding_model"],
    }


def set_llm_config(values: dict[str, str | None]) -> dict[str, str]:
    """Upsert admin-set values; ``None``/missing keys are left untouched.

    An explicit empty string clears the override (falls back to env/default).
    Returns the effective config after the update.
    """
    _set_overrides(LLM_SETTING_KEYS, values)
    return get_llm_config()


def get_retrieval_config() -> dict[str, int]:
    """Effective retrieval knobs: admin-set DB values over env defaults."""
    cfg = {
        "chunk_size": settings.kb_chunk_size,
        "chunk_overlap": settings.kb_chunk_overlap,
        "top_k": settings.kb_top_k,
    }
    for key in RETRIEVAL_KEYS:
        if key in _cache:
            try:
                cfg[key] = int(_cache[key])
            except (TypeError, ValueError):
                pass
    return cfg


def set_retrieval_config(values: dict[str, int | None]) -> dict[str, int]:
    """Upsert admin-set retrieval values; ``None``/missing keys are untouched.

    Returns the effective config after the update.
    """
    _set_overrides(
        RETRIEVAL_KEYS,
        {k: (str(v) if v is not None else None) for k, v in values.items()},
    )
    return get_retrieval_config()


def get_persona_config() -> dict[str, str]:
    """Effective AI persona: admin-set DB values over built-in defaults."""
    cfg = dict(PERSONA_DEFAULTS)
    for key in PERSONA_KEYS:
        if key in _cache:
            cfg[key] = _cache[key]
    return cfg


def set_persona_config(values: dict[str, str | None]) -> dict[str, str]:
    _set_overrides(PERSONA_KEYS, values)
    return get_persona_config()


def build_persona_prompt() -> str:
    """Persona-driven system-prompt prefix prepended to generation prompts."""
    p = get_persona_config()
    parts = [
        f"你是{p['persona_name']}，一位正在直播的主播。",
        f"性格特点：{p['persona_personality']}",
        f"说话风格：{p['persona_style']}",
        f"知识范围：{p['persona_knowledge_scope']}",
    ]
    topics = [t.strip() for t in p["persona_forbidden_topics"].split("，") if t.strip()]
    if topics:
        parts.append(f"禁止谈论：{'、'.join(topics)}")
    return "\n".join(parts)


def load_overrides() -> None:
    """Populate the cache from the DB at process start."""
    keys = LLM_SETTING_KEYS + PERSONA_KEYS + RETRIEVAL_KEYS
    rows = db.query(
        "SELECT key, value FROM settings WHERE key IN "
        f"({','.join('?' * len(keys))})",
        keys,
    )
    _cache.update({r["key"]: r["value"] for r in rows if r["value"]})


def mask_secret(value: str) -> str:
    """Mask a secret for display: keep the first 3 and last 4 chars."""
    if len(value) <= 8:
        return "****" if value else ""
    return f"{value[:3]}****{value[-4:]}"
