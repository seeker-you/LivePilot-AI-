"""Live session control and real-time WebSocket streaming."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

from ..bus import bus
from ..controller import controller
from ..db import db, loads
from ..deps import CurrentUser, require_roles
from ..idempotency import handle as idempotent
from ..models import ActionReviewRequest, SessionStartRequest
from ..security import decode_token


router = APIRouter(prefix="/api", tags=["live"])
manager_dep = Annotated[dict, Depends(require_roles("admin", "host"))]


@router.post("/live/start")
async def start_session(
    body: SessionStartRequest, request: Request, user: manager_dep
) -> dict:
    async def _start() -> dict:
        if any(controller.is_running(s["id"]) for s in _recent_sessions()):
            raise HTTPException(status_code=409, detail="已有正在进行的直播")
        try:
            session = await controller.start(
                body.title,
                body.event_rate,
                user["id"],
                platform=body.platform,
                room_id=body.room_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _session_out(session)

    return await idempotent(request, body, _start)


@router.post("/live/{session_id}/stop")
async def stop_session(
    session_id: str, request: Request, user: manager_dep
) -> dict:
    async def _stop() -> dict:
        session = await controller.stop(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="直播不存在")
        return _session_out(session)

    return await idempotent(request, None, _stop)


@router.get("/live/sessions")
def list_sessions(user: CurrentUser) -> list[dict]:
    rows = db.query(
        "SELECT * FROM live_sessions ORDER BY created_at DESC LIMIT 20"
    )
    return [_session_out(row) for row in rows]


@router.get("/live/{session_id}/snapshot")
def get_snapshot(session_id: str, user: CurrentUser) -> dict:
    return controller.snapshot(session_id)


@router.post("/live/{session_id}/actions/{action_id}/review")
async def review_action(
    session_id: str, action_id: int, body: ActionReviewRequest, user: manager_dep
) -> dict:
    action = db.query_one("SELECT * FROM actions WHERE id = ?", (action_id,))
    if not action:
        raise HTTPException(status_code=404, detail="动作不存在")
    db.execute(
        "UPDATE actions SET status = ?, reviewed_by = ? WHERE id = ?",
        (body.status.value, user["id"], action_id),
    )
    if body.status.value == "approved" and action["type"] in {"auto_reply", "welcome"}:
        await controller.push_approved(session_id, action)
    return {"ok": True}


@router.get("/live/{session_id}/queue")
def get_queue(session_id: str, user: CurrentUser) -> dict:
    return controller.queue_snapshot(session_id)


@router.post("/live/{session_id}/queue/pop")
async def pop_queue(session_id: str, user: manager_dep) -> dict:
    popped = await controller.pop_queue(session_id)
    return {"popped": popped}


@router.websocket("/ws/live/{session_id}")
async def live_websocket(websocket: WebSocket, session_id: str) -> None:
    token = websocket.query_params.get("token", "")
    claims = decode_token(token) if token else None
    if not claims:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    queue = await bus.subscribe(session_id)
    try:
        await websocket.send_json({"type": "hello", "data": controller.snapshot(session_id)})
        while True:
            message = await queue.get()
            await websocket.send_json(message)
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        raise
    finally:
        await bus.unsubscribe(session_id, queue)


def _recent_sessions() -> list[dict]:
    return db.query("SELECT * FROM live_sessions ORDER BY created_at DESC LIMIT 10")


def _session_out(session: dict) -> dict:
    session = dict(session)
    session["metrics"] = loads(session.get("metrics"), {})
    return session
