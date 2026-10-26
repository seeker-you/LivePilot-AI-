"""FastAPI dependencies for authentication and role checks."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import db
from .security import decode_token


bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录"
        )
    claims = decode_token(credentials.credentials)
    if not claims:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期"
        )
    user = db.query_one("SELECT * FROM users WHERE id = ?", (claims.get("sub"),))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在"
        )
    return user


def require_roles(*roles: str):
    def checker(user: Annotated[dict, Depends(get_current_user)]) -> dict:
        if user["role"] not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="权限不足"
            )
        return user

    return checker


CurrentUser = Annotated[dict, Depends(get_current_user)]

