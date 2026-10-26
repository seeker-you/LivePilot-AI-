"""Product knowledge CRUD endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import knowledge, knowledge_ai
from ..deps import CurrentUser, require_roles
from ..idempotency import handle as idempotent
from ..models import (
    KnowledgeCreate,
    KnowledgeEnrichOut,
    KnowledgeEnrichRequest,
    KnowledgeOrganizeOut,
    KnowledgeOrganizeRequest,
    KnowledgeOut,
)


router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
writer_dep = Annotated[dict, Depends(require_roles("admin", "host"))]


@router.get("", response_model=list[KnowledgeOut])
def list_items(_: CurrentUser) -> list[dict]:
    return knowledge.list_knowledge()


@router.post("", response_model=KnowledgeOut)
async def create_item(body: KnowledgeCreate, request: Request, _: writer_dep) -> dict:
    return await idempotent(request, body, lambda: knowledge.create_knowledge(body))


@router.delete("/{item_id}")
def delete_item(item_id: int, _: writer_dep) -> dict:
    if not knowledge.delete_knowledge(item_id):
        raise HTTPException(status_code=404, detail="知识条目不存在")
    return {"ok": True}


@router.get("/search")
async def search_items(q: str, _: CurrentUser) -> list[KnowledgeOut]:
    return await knowledge.search(q, limit=5)


@router.post("/organize", response_model=KnowledgeOrganizeOut)
async def organize_items(body: KnowledgeOrganizeRequest, _: writer_dep) -> dict:
    """AI 拆条：把零散素材整理成多条结构化知识（不落库，供前端预览）."""
    items = await knowledge_ai.organize_text(body.text, body.product, body.category)
    return {"items": items}


@router.post("/enrich", response_model=KnowledgeEnrichOut)
async def enrich_item(body: KnowledgeEnrichRequest, _: writer_dep) -> dict:
    """AI 补全：给单条草稿补标题/标签并优化描述（不落库，供前端回填）."""
    item = await knowledge_ai.enrich_item(
        body.title, body.content, body.product, body.category
    )
    return {"item": item}
