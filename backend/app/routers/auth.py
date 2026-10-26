"""Authentication endpoints and demo account seeding."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ..db import db, now
from ..deps import CurrentUser, require_roles
from ..models import LoginRequest, TokenResponse, UserPublic
from ..config import settings
from ..security import create_token, hash_password, verify_password


router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_public(user: dict) -> UserPublic:
    return UserPublic(
        id=user["id"],
        username=user["username"],
        display_name=user["display_name"],
        role=user["role"],
    )


def seed_demo_users() -> None:
    if db.query_one("SELECT id FROM users LIMIT 1"):
        return
    for username, display_name, role, password in [
        ("admin", "管理员", "admin", "admin123"),
        ("host", "主播小鹿", "host", "host123"),
        ("operator", "场控小林", "operator", "op123"),
    ]:
        digest, salt = hash_password(password)
        db.execute(
            """
            INSERT INTO users (username, display_name, password_hash, salt, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (username, display_name, digest, salt, role, now()),
        )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest) -> TokenResponse:
    user = db.query_one("SELECT * FROM users WHERE username = ?", (body.username,))
    if not user or not verify_password(body.password, user["salt"], user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_token(
        {"sub": str(user["id"]), "role": user["role"], "name": user["display_name"]},
        settings.jwt_expire_minutes * 60,
    )
    return TokenResponse(access_token=token, user=_user_public(user))


@router.get("/me", response_model=UserPublic)
def me(user: CurrentUser) -> UserPublic:
    return _user_public(user)


@router.post("/register", response_model=UserPublic)
def register(
    body: LoginRequest,
    _: Annotated[dict, Depends(require_roles("admin"))],
) -> UserPublic:
    if db.query_one("SELECT id FROM users WHERE username = ?", (body.username,)):
        raise HTTPException(status_code=409, detail="用户名已存在")
    digest, salt = hash_password(body.password)
    user_id = db.execute(
        """
        INSERT INTO users (username, display_name, password_hash, salt, role, created_at)
        VALUES (?, ?, ?, ?, 'operator', ?)
        """,
        (body.username, body.username, digest, salt, now()),
    )
    user = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    return _user_public(user)
