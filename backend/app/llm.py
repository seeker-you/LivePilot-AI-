"""OpenAI-compatible LLM client with a deterministic offline fallback.

When ``LLM_API_KEY`` is empty the project is fully runnable without external
services: classification, suggestions and reports are produced by the
heuristic engine. Set the key to route through a real model.

Network calls retry transient failures (timeouts, connection errors, 5xx)
with exponential backoff; unretryable 4xx responses and exhausted retries
fall through to the heuristic engine.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx

from .models import Intent, Sentiment
from .runtime_config import get_embedding_config, get_llm_config

RETRYABLE_STATUS = {500, 502, 503, 504, 429}
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 0.5

# Indirection so tests can observe backoff without really sleeping.
_sleep = asyncio.sleep


QUESTION_KEYWORDS = ("怎么", "如何", "为什么", "多少钱", "有没有", "能不能", "?", "？", "怎么买", "怎么拍")
PRAISE_KEYWORDS = ("好用", "喜欢", "好看", "绝了", "值", "回购", "赞", "棒", "不错")
COMPLAINT_KEYWORDS = ("差", "垃圾", "翻车", "退货", "客服", "不发货", "骗", "坑", "假")
PRICE_KEYWORDS = ("多少钱", "价格", "贵", "便宜", "优惠", "折扣", "券", "拍下")
ORDER_KEYWORDS = ("发货", "物流", "下单", "拍了", "付款", "库存", "尺码", "颜色", "链接", "在哪")
SPAM_KEYWORDS = ("加v", "加V", "刷单", "代运营", "私聊", "微信", "vx", "VX", "兼职")
SENSITIVE_KEYWORDS = ("退款", "投诉", "315", "假货", "诈骗")


@dataclass
class IntentResult:
    intent: Intent
    sentiment: Sentiment
    is_sensitive: bool


def heuristic_classify(text: str) -> IntentResult:
    lowered = text.lower()
    sentiment = Sentiment.neutral

    if any(k in text for k in SENSITIVE_KEYWORDS):
        return IntentResult(Intent.complaint, Sentiment.negative, True)
    if any(k in lowered for k in SPAM_KEYWORDS):
        return IntentResult(Intent.spam, Sentiment.negative, False)
    if any(k in text for k in COMPLAINT_KEYWORDS):
        sentiment = Sentiment.negative
    if any(k in text for k in PRAISE_KEYWORDS):
        sentiment = Sentiment.positive
    if any(k in text for k in PRICE_KEYWORDS):
        return IntentResult(Intent.price_inquiry, sentiment, False)
    if any(k in text for k in QUESTION_KEYWORDS):
        return IntentResult(Intent.question, sentiment, False)
    if any(k in text for k in ORDER_KEYWORDS):
        return IntentResult(Intent.order, sentiment, False)
    if sentiment is Sentiment.negative:
        return IntentResult(Intent.complaint, sentiment, False)
    if sentiment is Sentiment.positive:
        return IntentResult(Intent.praise, sentiment, False)
    return IntentResult(Intent.chitchat, Sentiment.neutral, False)


def _client(base_url: str, api_key: str) -> httpx.AsyncClient:
    # Ollama 等本地服务不需要 Key：没有 Key 时不发 Authorization 头。
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    # 本地模型首次加载可能耗时数十秒（如 8B 冷启动），给更宽松的超时。
    timeout = 180.0 if _is_local(base_url) else 15.0
    return httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout)


LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")


def _is_local(base_url: str) -> bool:
    return any(host in (base_url or "") for host in LOCAL_HOSTS)


def is_configured(cfg: dict[str, str] | None = None) -> bool:
    """有 Key，或指向本机服务（Ollama 等无需鉴权）即视为可用。"""
    cfg = cfg or get_llm_config()
    return bool(cfg["llm_api_key"]) or _is_local(cfg["llm_base_url"])


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return False


async def _post(
    path: str,
    payload: dict[str, Any],
    *,
    base_url: str | None = None,
    api_key: str | None = None,
) -> httpx.Response:
    """POST with exponential backoff on transient failures.

    ``base_url``/``api_key`` default to the chat endpoint so callers can point
    embeddings at a different provider.
    Raises the last exception after MAX_ATTEMPTS so callers can degrade.
    """
    cfg = get_llm_config()
    base = base_url or cfg["llm_base_url"]
    key = cfg["llm_api_key"] if api_key is None else api_key
    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            async with _client(base, key) as client:
                resp = await client.post(path, json=payload)
                resp.raise_for_status()
                return resp
        except Exception as exc:
            if not _is_retryable(exc):
                raise
            last_exc = exc
            if attempt < MAX_ATTEMPTS - 1:
                await _sleep(BACKOFF_BASE_SECONDS * (2**attempt))
    assert last_exc is not None
    raise last_exc


async def _chat_json(messages: list[dict[str, str]]) -> dict[str, Any] | None:
    """Call the model and return a parsed JSON object, or None on failure."""
    cfg = get_llm_config()
    if not is_configured(cfg):
        return None
    payload = {
        "model": cfg["llm_model"],
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    try:
        resp = await _post("/chat/completions", payload)
        data = resp.json()
        raw = data["choices"][0]["message"]["content"]
        return json.loads(raw)
    except Exception:
        return None


async def classify(text: str) -> IntentResult:
    """Classify intent and sentiment, preferring the model when available."""
    result = await _chat_json(
        [
            {
                "role": "system",
                "content": (
                    "你是直播弹幕分类器。输出 JSON：{\"intent\": "
                    "\"question|praise|complaint|price_inquiry|order|spam|chitchat\", "
                    "\"sentiment\": \"positive|neutral|negative\", "
                    "\"sensitive\": true|false}"
                ),
            },
            {"role": "user", "content": text},
        ]
    )
    if result:
        try:
            return IntentResult(
                intent=Intent(result.get("intent", "chitchat")),
                sentiment=Sentiment(result.get("sentiment", "neutral")),
                is_sensitive=bool(result.get("sensitive", False)),
            )
        except ValueError:
            pass
    return heuristic_classify(text)


async def generate_text(system: str, user: str) -> str | None:
    """Non-streaming generation; returns None when the model is unavailable."""
    cfg = get_llm_config()
    if not is_configured(cfg):
        return None
    payload = {
        "model": cfg["llm_model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.6,
    }
    try:
        resp = await _post("/chat/completions", payload)
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception:
        return None


async def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Return dense embeddings, or None when no endpoint/key is configured.

    向量接口可用独立的 base_url / key（如对话走 OpenAI、向量走本地 Ollama），
    两者留空时自动复用对话配置。
    """
    emb = get_embedding_config()
    if not emb["api_key"] and not _is_local(emb["base_url"]):
        return None
    payload = {
        "model": emb["model"],
        "input": texts,
    }
    try:
        resp = await _post(
            "/embeddings",
            payload,
            base_url=emb["base_url"],
            api_key=emb["api_key"],
        )
        data = resp.json()
        return [item["embedding"] for item in data["data"]]
    except Exception:
        return None
