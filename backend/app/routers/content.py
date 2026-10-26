"""Content generation endpoints with SSE streaming."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from .. import content
from ..deps import CurrentUser
from ..models import ContentRequest


router = APIRouter(prefix="/api/content", tags=["content"])


@router.get("/kinds")
def list_kinds(_: CurrentUser) -> list[dict]:
    return content.list_kinds()


@router.post("/generate")
async def generate(body: ContentRequest, _: CurrentUser) -> StreamingResponse:
    async def stream():
        try:
            text, sources = await content.generate(
                body.kind, body.topic, body.style, body.context
            )
            for i in range(0, len(text), 12):
                chunk = text[i : i + 12]
                yield f"data: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.015)
            yield f"data: {json.dumps({'done': True, 'sources': sources}, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

