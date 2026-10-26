"""Script library CRUD endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from .. import script_manager
from ..deps import CurrentUser, require_roles
from ..models import ScriptCreate, ScriptUpdate


router = APIRouter(prefix="/api/scripts", tags=["scripts"])
writer_dep = Annotated[dict, Depends(require_roles("admin", "host"))]


@router.get("")
def list_scripts(_: CurrentUser) -> list[dict]:
    return script_manager.list_scripts()


@router.post("", status_code=201)
def create_script(body: ScriptCreate, _: writer_dep) -> dict:
    return script_manager.create_script(body.title, body.content, body.type, body.tags)


@router.patch("/{script_id}")
def update_script(script_id: int, body: ScriptUpdate, _: writer_dep) -> dict:
    result = script_manager.update_script(
        script_id, {k: v for k, v in body.model_dump().items() if v is not None}
    )
    if not result:
        raise HTTPException(status_code=404, detail="话术不存在")
    return result


@router.delete("/{script_id}")
def delete_script(script_id: int, _: writer_dep) -> dict:
    if not script_manager.delete_script(script_id):
        raise HTTPException(status_code=404, detail="话术不存在")
    return {"ok": True}

