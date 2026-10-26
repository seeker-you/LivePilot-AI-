"""Idempotency-Key support for mutating endpoints.

A client that retries a timed-out POST (mobile networks, gateway timeouts)
must not trigger the side effect twice. Endpoints wrapped with ``handle``
store the first response under the caller-supplied ``Idempotency-Key``;
replays return the stored response instead of re-executing.

Concurrent duplicates get a 409 while the first request is still running,
and a key reused with a different body is rejected with 422 — same
semantics as Stripe-style idempotency.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, Request

from .db import db, dumps, now

# Replay windows longer than this are forgotten so the table stays small.
TTL_SECONDS = 24 * 3600


async def handle(
    request: Request,
    body: Any,
    handler: Callable[[], Awaitable[Any]],
) -> Any:
    """Execute ``handler`` once per Idempotency-Key, replaying cached responses."""
    key = request.headers.get("idempotency-key", "").strip()
    if not key:
        return await handler()
    if len(key) > 128:
        raise HTTPException(status_code=422, detail="Idempotency-Key 过长")

    request_hash = _request_hash(request, body)
    _purge_expired()

    row = db.query_one("SELECT * FROM idempotency WHERE key = ?", (key,))
    if row:
        if row["request_hash"] != request_hash:
            raise HTTPException(
                status_code=422, detail="Idempotency-Key 已被其他请求体使用"
            )
        if row["status"] == "processing":
            raise HTTPException(status_code=409, detail="重复请求正在处理中，请稍后查询")
        return _replay(row)

    db.execute(
        "INSERT OR IGNORE INTO idempotency (key, request_hash, status, created_at)"
        " VALUES (?, ?, 'processing', ?)",
        (key, request_hash, now()),
    )
    row = db.query_one("SELECT * FROM idempotency WHERE key = ?", (key,))
    if row["request_hash"] != request_hash:
        raise HTTPException(
            status_code=422, detail="Idempotency-Key 已被其他请求体使用"
        )

    try:
        result = await handler()
    except HTTPException as exc:
        # Client errors are part of the contract: cache them too so a replay
        # gets the same answer instead of re-validating against new state.
        db.execute(
            "UPDATE idempotency SET status = 'done', response_code = ?,"
            " response_body = ? WHERE key = ?",
            (exc.status_code, dumps({"detail": exc.detail}), key),
        )
        raise
    except Exception:
        # Unexpected failure: release the key so the client may retry cleanly.
        db.execute("DELETE FROM idempotency WHERE key = ?", (key,))
        raise

    db.execute(
        "UPDATE idempotency SET status = 'done', response_code = 200,"
        " response_body = ? WHERE key = ?",
        (dumps(result), key),
    )
    return result


def _replay(row: dict) -> Any:
    """Return the cached response, reproducing error status codes."""
    if row["response_code"] != 200:
        body = json.loads(row["response_body"])
        raise HTTPException(
            status_code=row["response_code"], detail=body.get("detail")
        )
    return json.loads(row["response_body"])


def _request_hash(request: Request, body: Any) -> str:
    if hasattr(body, "model_dump"):
        body = body.model_dump()
    digest = hashlib.sha256()
    digest.update(f"{request.method} {request.url.path}".encode())
    digest.update(dumps(body if body is not None else {}).encode())
    return digest.hexdigest()


def _purge_expired() -> None:
    db.execute(
        "DELETE FROM idempotency WHERE created_at < ?", (now() - TTL_SECONDS,)
    )
