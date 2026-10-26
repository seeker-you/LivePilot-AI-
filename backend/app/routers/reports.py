"""Replay aggregation and report generation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import replay
from ..deps import CurrentUser


router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/{session_id}/metrics")
def get_metrics(session_id: str, _: CurrentUser) -> dict:
    metrics = replay.aggregate_session(session_id)
    if not metrics["session"]:
        raise HTTPException(status_code=404, detail="直播不存在")
    return metrics


@router.post("/{session_id}/generate")
async def generate_report(session_id: str, _: CurrentUser) -> dict:
    metrics = replay.aggregate_session(session_id)
    if not metrics["session"]:
        raise HTTPException(status_code=404, detail="直播不存在")
    report = await replay.generate_report(metrics)
    return {"session_id": session_id, "report": report, "metrics": metrics}

