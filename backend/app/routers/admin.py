"""Admin-only system administration: LLM config, AI persona, user management."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import db, now
from ..deps import require_roles
from ..models import PasswordReset, PersonaUpdate, UserCreate, UserUpdate
from ..runtime_config import (
    LLM_SETTING_KEYS,
    get_embedding_config,
    get_llm_config,
    get_persona_config,
    get_retrieval_config,
    mask_secret,
    set_llm_config,
    set_persona_config,
    set_retrieval_config,
)
from ..security import hash_password
from ..llm import _is_local, _post, is_configured

router = APIRouter(prefix="/api/admin", tags=["admin"])
admin_dep = Annotated[dict, Depends(require_roles("admin"))]

VALID_ROLES = {"admin", "host", "operator"}


class LlmConfigUpdate(BaseModel):
    # None/missing = leave unchanged; empty string = clear back to env default.
    llm_api_key: str | None = Field(default=None, max_length=200)
    llm_base_url: str | None = Field(default=None, max_length=300)
    llm_model: str | None = Field(default=None, max_length=100)
    llm_embedding_model: str | None = Field(default=None, max_length=100)
    # 向量接口独立配置；留空表示复用上面的对话接口。
    llm_embedding_base_url: str | None = Field(default=None, max_length=300)
    llm_embedding_api_key: str | None = Field(default=None, max_length=200)


class LlmConfigTest(BaseModel):
    """可选的“未保存”表单值：用于先测试、再保存。"""

    llm_api_key: str | None = Field(default=None, max_length=200)
    llm_base_url: str | None = Field(default=None, max_length=300)
    llm_model: str | None = Field(default=None, max_length=100)
    llm_embedding_model: str | None = Field(default=None, max_length=100)
    llm_embedding_base_url: str | None = Field(default=None, max_length=300)
    llm_embedding_api_key: str | None = Field(default=None, max_length=200)


class RetrievalConfigUpdate(BaseModel):
    # None/missing = leave unchanged.
    chunk_size: int | None = Field(default=None, ge=50, le=4000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=800)
    top_k: int | None = Field(default=None, ge=1, le=50)


def _config_out() -> dict[str, Any]:
    cfg = get_llm_config()
    emb = get_embedding_config()
    masked = dict(cfg)
    masked["llm_api_key"] = mask_secret(cfg["llm_api_key"])
    masked["llm_embedding_api_key"] = mask_secret(cfg["llm_embedding_api_key"])
    masked["llm_configured"] = is_configured(cfg)
    masked["embedding_configured"] = bool(emb["api_key"]) or _is_local(emb["base_url"])
    return masked


@router.get("/llm-config")
async def read_llm_config(user: admin_dep) -> dict[str, Any]:
    return _config_out()


@router.put("/llm-config")
async def update_llm_config(body: LlmConfigUpdate, user: admin_dep) -> dict[str, Any]:
    values = body.model_dump()
    current = get_llm_config()
    # A masked key echoed back unchanged means "keep the current one".
    for key in ("llm_api_key", "llm_embedding_api_key"):
        value = values.get(key)
        if value and "****" in value and mask_secret(current[key]) == value:
            values[key] = None
    set_llm_config({k: v for k, v in values.items() if k in LLM_SETTING_KEYS})
    return _config_out()


@router.post("/llm-config/test")
async def test_llm_config(
    user: admin_dep, body: LlmConfigTest | None = None
) -> dict[str, Any]:
    """Test the effective (or unsaved form) credentials for chat and vectors."""
    cfg = get_llm_config()
    if body is not None:
        # 表单里没填的字段不覆盖已保存配置
        cfg.update({k: v for k, v in body.model_dump(exclude_none=True).items() if v})
    if not is_configured(cfg):
        raise HTTPException(
            status_code=422,
            detail="未配置 API Key（本地 Ollama 可将接口地址填为 http://localhost:11434/v1）",
        )
    try:
        resp = await _post(
            "/chat/completions",
            {
                "model": cfg["llm_model"],
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
            base_url=cfg["llm_base_url"],
            api_key=cfg["llm_api_key"],
        )
    except Exception as exc:
        detail = str(exc)
        if getattr(exc, "response", None) is not None:
            detail = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        raise HTTPException(status_code=502, detail=f"对话连通失败：{detail}") from exc

    result: dict[str, Any] = {
        "ok": True,
        "status": resp.status_code,
        "model": cfg["llm_model"],
    }
    emb = {
        "base_url": cfg["llm_embedding_base_url"] or cfg["llm_base_url"],
        "api_key": cfg["llm_embedding_api_key"] or cfg["llm_api_key"],
        "model": cfg["llm_embedding_model"],
    }
    if emb["api_key"] or _is_local(emb["base_url"]):
        try:
            eresp = await _post(
                "/embeddings",
                {"model": emb["model"], "input": ["ping"]},
                base_url=emb["base_url"],
                api_key=emb["api_key"],
            )
            result["embedding"] = {
                "ok": True,
                "status": eresp.status_code,
                "model": emb["model"],
            }
        except Exception as exc:
            detail = str(exc)
            if getattr(exc, "response", None) is not None:
                detail = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            result["embedding"] = {"ok": False, "error": detail, "model": emb["model"]}
    return result


# ── 知识库检索（分块与 top-k） ─────────────────────────────────────────


@router.get("/retrieval-config")
async def read_retrieval_config(user: admin_dep) -> dict[str, Any]:
    return get_retrieval_config()


@router.put("/retrieval-config")
async def update_retrieval_config(
    body: RetrievalConfigUpdate, user: admin_dep
) -> dict[str, Any]:
    values = {k: v for k, v in body.model_dump().items() if v is not None}
    if (
        "chunk_size" in values
        and "chunk_overlap" in values
        and values["chunk_overlap"] >= values["chunk_size"]
    ):
        raise HTTPException(status_code=422, detail="重叠长度需小于分块大小")
    return set_retrieval_config(values)


# ── AI 人设（系统提示词） ────────────────────────────────────────────────


@router.get("/persona")
async def read_persona(user: admin_dep) -> dict[str, Any]:
    return get_persona_config()


@router.put("/persona")
async def update_persona(body: PersonaUpdate, user: admin_dep) -> dict[str, Any]:
    return set_persona_config(
        {k: v for k, v in body.model_dump().items() if v is not None}
    )


# ── 用户与权限管理 ──────────────────────────────────────────────────────


def _user_out(user: dict) -> dict[str, Any]:
    return {k: user[k] for k in ("id", "username", "display_name", "role", "created_at")}


def _admin_count() -> int:
    return db.query_one("SELECT COUNT(*) AS c FROM users WHERE role = 'admin'")["c"]


def _get_user_or_404(user_id: int) -> dict:
    user = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@router.get("/users")
async def list_users(user: admin_dep) -> list[dict[str, Any]]:
    return [_user_out(u) for u in db.query("SELECT * FROM users ORDER BY id")]


@router.post("/users", status_code=201)
async def create_user(body: UserCreate, user: admin_dep) -> dict[str, Any]:
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=422, detail="角色必须是 admin/host/operator")
    if db.query_one("SELECT id FROM users WHERE username = ?", (body.username,)):
        raise HTTPException(status_code=409, detail="用户名已存在")
    digest, salt = hash_password(body.password)
    new_id = db.execute(
        """
        INSERT INTO users (username, display_name, password_hash, salt, role, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (body.username, body.display_name or body.username, digest, salt, body.role, now()),
    )
    return _user_out(_get_user_or_404(new_id))


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int, body: UserUpdate, user: admin_dep
) -> dict[str, Any]:
    target = _get_user_or_404(user_id)
    if body.role is not None:
        if body.role not in VALID_ROLES:
            raise HTTPException(status_code=422, detail="角色必须是 admin/host/operator")
        if user_id == user["id"]:
            raise HTTPException(status_code=422, detail="不能修改自己的角色")
        if target["role"] == "admin" and body.role != "admin" and _admin_count() <= 1:
            raise HTTPException(status_code=422, detail="系统至少需要保留一名管理员")
    if body.display_name is not None or body.role is not None:
        db.execute(
            "UPDATE users SET display_name = COALESCE(?, display_name), "
            "role = COALESCE(?, role) WHERE id = ?",
            (body.display_name, body.role, user_id),
        )
    return _user_out(_get_user_or_404(user_id))


@router.put("/users/{user_id}/password")
async def reset_password(
    user_id: int, body: PasswordReset, user: admin_dep
) -> dict[str, bool]:
    _get_user_or_404(user_id)
    digest, salt = hash_password(body.password)
    db.execute(
        "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
        (digest, salt, user_id),
    )
    return {"ok": True}


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, user: admin_dep) -> dict[str, bool]:
    target = _get_user_or_404(user_id)
    if user_id == user["id"]:
        raise HTTPException(status_code=422, detail="不能删除自己")
    if target["role"] == "admin" and _admin_count() <= 1:
        raise HTTPException(status_code=422, detail="系统至少需要保留一名管理员")
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return {"ok": True}
