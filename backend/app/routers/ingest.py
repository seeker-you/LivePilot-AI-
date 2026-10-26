"""Inbound webhooks for external event collectors.

Currently serves wxlivespy (微信视频号): it watches the Channels backend via
browser automation and POSTs decoded events here while a wxlive session is
live. The endpoint is machine-to-machine: it bypasses the per-client API rate
limiter and authenticates with a shared token instead of JWT.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..config import settings
from ..platform import wxlive_dispatch

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.post("/wxlive")
async def ingest_wxlive(request: Request) -> dict:
    if settings.wxlive_ingest_token:
        token = request.headers.get("x-ingest-token", "")
        if token != settings.wxlive_ingest_token:
            raise HTTPException(status_code=401, detail="无效的接入 Token")

    try:
        data = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON") from exc

    # wxlivespy 批量格式: {"events": [...]}; 宽容接受单个事件对象
    events = data.get("events") if isinstance(data, dict) else None
    if events is None and isinstance(data, dict) and "decoded_type" in data:
        events = [data]
    if not isinstance(events, list):
        raise HTTPException(status_code=422, detail="缺少 events 数组")

    delivered = wxlive_dispatch([e for e in events if isinstance(e, dict)])
    return {"ok": True, "received": len(events), "delivered": delivered}
