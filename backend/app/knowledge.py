"""Product knowledge store with hybrid retrieval.

Search uses BM25 sparse ranking by default. When an embedding endpoint is
configured (``LLM_API_KEY`` present) it adds dense cosine ranking and fuses the
two lists with reciprocal rank fusion, otherwise it degrades to BM25-only so the
project stays runnable offline.
"""

from __future__ import annotations

import logging
from typing import Any

from . import llm, retriever
from .db import db, dumps, loads, now
from .models import KnowledgeCreate
from .runtime_config import get_llm_config, get_retrieval_config


logger = logging.getLogger("livepilot.knowledge")


def list_knowledge(limit: int = 100) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT * FROM knowledge ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    for row in rows:
        row["tags"] = loads(row.get("tags"), [])
    return rows


async def create_knowledge(item: KnowledgeCreate) -> dict[str, Any]:
    item_id = db.execute(
        """
        INSERT INTO knowledge (title, product, category, content, tags, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (item.title, item.product, item.category, item.content, dumps(item.tags), now()),
    )
    await _store_embedding(item_id, item)
    return get_knowledge(item_id)


def get_knowledge(item_id: int) -> dict[str, Any] | None:
    row = db.query_one("SELECT * FROM knowledge WHERE id = ?", (item_id,))
    if row:
        row["tags"] = loads(row.get("tags"), [])
    return row


def delete_knowledge(item_id: int) -> bool:
    db.execute("DELETE FROM knowledge_embeddings WHERE knowledge_id = ?", (item_id,))
    cur = db.execute("DELETE FROM knowledge WHERE id = ?", (item_id,))
    return cur > 0


def _chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """按字符数切块（中文无需分词），块间保留 overlap 个字符的重叠。

    overlap 会被钳制到 size-1，避免退化成分块过碎或死循环。
    """
    if not text:
        return []
    if size <= 0 or len(text) <= size:
        return [text]
    overlap = min(max(0, overlap), size - 1)
    step = size - overlap
    return [text[i : i + size] for i in range(0, len(text), step)]


async def search(query: str, limit: int | None = None) -> list[dict[str, Any]]:
    """块级混合检索：content 按当前 chunk_size/overlap 切块做 BM25，
    命中块映射回所属知识条目；配置了向量接口时再融合条目级余弦排名。
    返回最多 top_k（默认取配置，显式 limit 优先）个知识条目。
    """
    items = list_knowledge()
    if not items:
        return []

    cfg = get_retrieval_config()
    top_k = limit if limit is not None else cfg["top_k"]

    docs: list[dict[str, Any]] = []
    doc_owner: list[int] = []
    for it in items:
        for chunk in _chunk_text(
            it["content"], cfg["chunk_size"], cfg["chunk_overlap"]
        ):
            docs.append(
                {
                    "title": it["title"],
                    "product": it["product"],
                    "category": it["category"],
                    "content": chunk,
                    "tags": " ".join(it["tags"]),
                }
            )
            doc_owner.append(it["id"])
    bm_ranked = retriever.bm25_rank(docs, query)

    # 块排名折叠回条目级：取该条目所有命中块的最高分
    best_scores: dict[int, float] = {}
    for doc_idx, score in bm_ranked:
        kbid = doc_owner[doc_idx]
        best_scores[kbid] = max(best_scores.get(kbid, 0.0), score)
    item_ids = [it["id"] for it in items]
    bm_ranked_idx = sorted(
        ((item_ids.index(kbid), score) for kbid, score in best_scores.items()),
        key=lambda pair: pair[1],
        reverse=True,
    )

    if get_llm_config()["llm_api_key"]:
        vectors = _load_vectors()
        if vectors:
            query_vec = await llm.embed_texts([query])
            if query_vec:
                dense_ranked = retriever.cosine_rank(query_vec[0], vectors, item_ids)
                fused = retriever.rrf_fuse([bm_ranked_idx, dense_ranked])
                return [items[idx] for idx, _ in fused[:top_k]]

    return [items[idx] for idx, _ in bm_ranked_idx[:top_k]]


async def _store_embedding(item_id: int, item: KnowledgeCreate) -> None:
    cfg = get_llm_config()
    if not cfg["llm_api_key"]:
        return
    text = f"{item.title} {item.product} {item.content} " + " ".join(item.tags)
    try:
        vectors = await llm.embed_texts([text])
        if not vectors:
            return
        vector = vectors[0]
        db.execute(
            """
            INSERT OR REPLACE INTO knowledge_embeddings
            (knowledge_id, model, dim, vector, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                item_id,
                cfg["llm_embedding_model"],
                len(vector),
                dumps(vector),
                now(),
            ),
        )
    except Exception:
        # Embedding failures must never block knowledge ingestion.
        logger.warning("Failed to embed knowledge item %s", item_id, exc_info=True)


def _load_vectors() -> dict[int, list[float]]:
    rows = db.query("SELECT knowledge_id, vector FROM knowledge_embeddings")
    return {row["knowledge_id"]: loads(row["vector"], []) for row in rows}


async def seed_defaults() -> None:
    """Insert a few starter entries so the RAG flow works out of the box."""
    if list_knowledge(limit=1):
        return
    defaults = [
        KnowledgeCreate(
            title="主推商品卖点",
            product="熬夜修护面霜",
            category="product",
            content=(
                "核心卖点：玻色因+烟酰胺双效修护；28 天改善暗沉；"
                "质地清爽不粘腻，敏感肌可用。今晚直播价 199 元，买一送一，"
                "前 100 名加赠同款小样。"
            ),
            tags=["面霜", "修护", "熬夜", "敏感肌"],
        ),
        KnowledgeCreate(
            title="物流与售后政策",
            product="通用",
            category="policy",
            content=(
                "现货 48 小时内发货，默认中通/圆通，偏远地区顺丰补差。"
                "支持 7 天无理由退换，运费险由直播间承担。"
            ),
            tags=["发货", "物流", "退换", "运费险"],
        ),
        KnowledgeCreate(
            title="直播专属优惠",
            product="通用",
            category="policy",
            content=(
                "关注主播领取 10 元无门槛券；满 399 减 40；"
                "榜一观众可额外申请专属赠品，联系场控登记。"
            ),
            tags=["优惠", "券", "满减", "福利"],
        ),
    ]
    for item in defaults:
        await create_knowledge(item)

