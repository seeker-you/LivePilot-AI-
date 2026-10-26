"""Offline retrieval quality eval for the knowledge base.

Runs BM25 (no embedding key required) over the seeded knowledge items and
reports hit@1 / hit@3 for a small set of labeled queries. Semantic-miss cases
are flagged as the ones dense embeddings are meant to recover.

Usage: python scripts/retrieval_eval.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import knowledge, retriever


CASES: list[tuple[str, str, str]] = [
    # (query, expected title, kind)
    ("物流一般几天到", "物流与售后政策", "lexical"),
    ("支持七天无理由吗", "物流与售后政策", "lexical"),
    ("有没有优惠券", "直播专属优惠", "lexical"),
    ("敏感肌能用吗", "主推商品卖点", "lexical"),
    ("熬夜用什么修护", "主推商品卖点", "lexical"),
    ("油皮可以用吗", "主推商品卖点", "semantic"),
    ("出油厉害怎么办", "主推商品卖点", "semantic"),
    ("这个多少钱", "主推商品卖点", "semantic"),
]


def main() -> None:
    if not knowledge.list_knowledge(limit=1):
        asyncio.run(knowledge.seed_defaults())

    items = knowledge.list_knowledge()
    docs = [
        {
            "title": it["title"],
            "product": it["product"],
            "category": it["category"],
            "content": it["content"],
            "tags": " ".join(it["tags"]),
        }
        for it in items
    ]

    hit1 = 0
    print(f"知识库条目：{len(items)}（语料较小，top-3 区分度有限，主要看 hit@1 与 rank）\n")
    print(f"{'query':<16} {'kind':<9} {'top1':<12} {'rank':<5} hit@1")
    for query, expected, kind in CASES:
        ranked = retriever.bm25_rank(docs, query)
        top_titles = [items[idx]["title"] for idx, _ in ranked[:3]]
        h1 = top_titles and top_titles[0] == expected
        hit1 += int(h1)
        rank = next(
            (i + 1 for i, (idx, _) in enumerate(ranked) if items[idx]["title"] == expected),
            0,
        )
        print(
            f"{query:<16} {kind:<9} {(top_titles[0] if top_titles else '-'):<12} "
            f"{rank:<5} {h1}"
        )

    n = len(CASES)
    print(f"\nBM25 hit@1 = {hit1}/{n} ({hit1 / n:.0%})")
    lexical = [c for c in CASES if c[2] == "lexical"]
    l_hit = sum(
        1
        for c in lexical
        if retriever.bm25_rank(docs, c[0])
        and items[retriever.bm25_rank(docs, c[0])[0][0]]["title"] == c[1]
    )
    print(f"其中词汇类 query hit@1 = {l_hit}/{len(lexical)}")
    print(
        "\n语义类 miss（油皮/出油/多少钱）是稠密向量检索的补强点："
        "这类问题与知识库无表面词汇重叠，仅靠 BM25 无法命中。"
    )


if __name__ == "__main__":
    main()
