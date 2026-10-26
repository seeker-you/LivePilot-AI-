"""Moderation rule read/update endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import rules
from ..deps import CurrentUser, require_roles
from ..idempotency import handle as idempotent
from ..models import RulesUpdate


router = APIRouter(prefix="/api/rules", tags=["rules"])
writer_dep = Annotated[dict, Depends(require_roles("admin", "host"))]


@router.get("")
def get_rules(_: CurrentUser) -> dict:
    return rules.get_rules()


@router.put("")
async def update_rules(body: RulesUpdate, request: Request, _: writer_dep) -> dict:
    async def _apply() -> dict:
        try:
            return rules.apply_rules(body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return await idempotent(request, body, _apply)

