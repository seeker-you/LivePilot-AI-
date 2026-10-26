"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import knowledge, runtime_config
from . import script_manager
from .config import settings
from .db import db, now
from .ratelimit import api_limiter, client_key, login_limiter
from .routers import (
    admin,
    assist,
    auth,
    content,
    ingest,
    knowledge as knowledge_router,
    live,
    reports,
    rules,
    scripts,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("livepilot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Any 'live' row is orphaned after a process restart because sessions run
    # in-memory. Close them so the UI never points at a dead stream.
    db.execute(
        "UPDATE live_sessions SET status = 'ended', ended_at = ? WHERE status = 'live'",
        (now(),),
    )
    auth.seed_demo_users()
    await knowledge.seed_defaults()
    script_manager.seed_default_scripts()
    runtime_config.load_overrides()
    logger.info("LivePilot backend ready")
    yield


app = FastAPI(
    title="LivePilot API",
    version="0.1.0",
    description="直播 AI 运营中台：实时场控 + 话术辅助 + 数据复盘",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limiting(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api"):
        return await call_next(request)

    if path == "/api/auth/login":
        allowed, retry_after = login_limiter.hit(f"login:{client_key(request)}")
    elif path == "/api/health":
        return await call_next(request)
    elif path.startswith("/api/ingest/"):
        # Machine-to-machine webhooks: high event rates, token-authenticated
        # by their own routers, exempt from the per-client budget.
        return await call_next(request)
    else:
        allowed, retry_after = api_limiter.hit(f"api:{client_key(request)}")

    if not allowed:
        logger.warning("Rate limited %s %s", request.method, path)
        return JSONResponse(
            status_code=429,
            content={"detail": "请求过于频繁，请稍后再试"},
            headers={"Retry-After": str(max(1, int(retry_after) + 1))},
        )
    return await call_next(request)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s -> %s (%.1fms)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试"},
    )


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name}


app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(live.router)
app.include_router(knowledge_router.router)
app.include_router(assist.router)
app.include_router(content.router)
app.include_router(reports.router)
app.include_router(rules.router)
app.include_router(ingest.router)
app.include_router(scripts.router)
