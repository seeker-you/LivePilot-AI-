"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    app_name: str = "LivePilot"
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8000"))
    database_url: str = os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "livepilot.db"))
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-secret-change-me")
    jwt_expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "720"))
    # OpenAI-compatible endpoint. Leave LLM_API_KEY empty to use the built-in
    # heuristic engine so the project runs without external services.
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    llm_embedding_model: str = os.getenv("LLM_EMBEDDING_MODEL", "text-embedding-3-small")
    # Optional separate endpoint for embeddings (e.g. chat on OpenAI, vectors on
    # a local Ollama). Empty values fall back to the chat base_url / api_key.
    llm_embedding_base_url: str = os.getenv("LLM_EMBEDDING_BASE_URL", "")
    llm_embedding_api_key: str = os.getenv("LLM_EMBEDDING_API_KEY", "")
    # Knowledge retrieval: chunking and top-k defaults. Admins can override
    # these through the admin UI (settings table wins over env/defaults).
    kb_chunk_size: int = int(os.getenv("KB_CHUNK_SIZE", "400"))
    kb_chunk_overlap: int = int(os.getenv("KB_CHUNK_OVERLAP", "50"))
    kb_top_k: int = int(os.getenv("KB_TOP_K", "3"))
    # Real-platform ingestion:
    # - Douyin direct connect needs no configuration (platform "douyin").
    # - DouyinBarrageGrab legacy mode (platform "douyin_grab") broadcasts
    #   decoded events on a local WebSocket.
    # - Wxlive (微信视频号): wxlivespy pushes events via HTTP webhook; set a
    #   token to require it on the ingest endpoint (empty = allow, local demo).
    douyin_ws_url: str = os.getenv("DOUYIN_WS_URL", "ws://127.0.0.1:8888")
    wxlive_ingest_token: str = os.getenv("WXLIVE_INGEST_TOKEN", "")
    cors_origins: tuple[str, ...] = tuple(
        o.strip()
        for o in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if o.strip()
    )


settings = Settings()
