"""Real-time script assistance with SSE streaming."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from .. import knowledge, llm
from ..deps import CurrentUser
from ..models import AssistRequest
from ..runtime_config import build_persona_prompt


router = APIRouter(prefix="/api/assist", tags=["assist"])


@router.post("/stream")
async def assist_stream(body: AssistRequest, _: CurrentUser) -> StreamingResponse:
    hits = knowledge.search(body.query, limit=3)

    async def stream():
        try:
            context = _context_prompt(body.query, hits, body.context)
            generated = await llm.generate_text(
                build_persona_prompt()
                + "\n你是直播话术助手。根据知识库和实时上下文，生成主播可直接口播的"
                "下一句话术。要求自然、有卖点、可落地，控制在 80 字以内。",
                context,
            )
            text = generated or _fallback_suggestion(body.query, hits)
            # Emit in small chunks so the frontend shows a true streaming feel.
            for i in range(0, len(text), 8):
                chunk = text[i : i + 8]
                yield f"data: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.02)
            yield f"data: {json.dumps({'done': True, 'sources': [h['title'] for h in hits]}, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _context_prompt(query: str, hits: list[dict], context: list[dict]) -> str:
    kb = "\n".join(f"- {h['title']}: {h['content']}" for h in hits) or "（无匹配知识）"
    live = "\n".join(
        f"- {c.get('user', '观众')}: {c.get('text', c.get('type', ''))}"
        for c in context[-8:]
    ) or "（暂无实时弹幕）"
    return f"主播提问/需求：{query}\n知识库：\n{kb}\n最近弹幕：\n{live}"


def _fallback_suggestion(query: str, hits: list[dict]) -> str:
    if hits:
        item = hits[0]
        return (
            f"宝宝们看过来～关于「{query}」，我重点讲三点："
            f"{item['content'][:60]}。现在下单还能享受直播间专属优惠，"
            "有问题随时扣弹幕，主播在线解答！"
        )
    return (
        f"关于「{query}」，很多宝宝都在问。大家先点个关注，"
        "我马上详细讲解，福利也会在直播间限时放出，不要错过哦！"
    )

