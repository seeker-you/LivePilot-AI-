"""Hybrid retrieval primitives: BM25 sparse + optional dense + RRF fusion.

BM25 runs offline with no dependencies and is the default. Dense embeddings
are only used when an embedding endpoint is configured; otherwise the caller
falls back to BM25-only. Reciprocal Rank Fusion (RRF) merges both rankings
without needing to calibrate their score scales.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any


# Title and tags matter more for short danmaku questions than the body text.
FIELD_WEIGHTS = {
    "title": 3.0,
    "product": 2.0,
    "tags": 2.5,
    "category": 0.5,
    "content": 1.0,
}

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[a-zA-Z0-9]+")


def tokenize(text: str) -> list[str]:
    """Character unigrams + bigrams for CJK, whole tokens for ASCII.

    Bigrams carry word-boundary signal without a segmentation dependency;
    unigrams keep recall for single-character anchors like 券/价/退.
    """
    lowered = text.lower()
    tokens: list[str] = []
    for run in _CJK_RE.findall(lowered):
        tokens.extend(run)
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    tokens.extend(_WORD_RE.findall(lowered))
    return tokens


class BM25:
    """Standard Okapi BM25 over weighted document fields."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.N = 0
        self.df: Counter[str] = Counter()
        self.field_freqs: list[dict[str, Counter[str]]] = []
        self.field_lens: list[dict[str, int]] = []
        self.avg_field_len: dict[str, float] = {f: 1.0 for f in FIELD_WEIGHTS}

    def fit(self, docs: list[dict[str, str]]) -> "BM25":
        self.N = len(docs)
        self.field_freqs = []
        self.field_lens = []
        self.df = Counter()
        for doc in docs:
            field_counts: dict[str, Counter[str]] = {}
            field_lens: dict[str, int] = {}
            seen: set[str] = set()
            for field in FIELD_WEIGHTS:
                tokens = tokenize(doc.get(field, "") or "")
                field_counts[field] = Counter(tokens)
                field_lens[field] = len(tokens)
                seen.update(tokens)
            for term in seen:
                self.df[term] += 1
            self.field_freqs.append(field_counts)
            self.field_lens.append(field_lens)
        for field in FIELD_WEIGHTS:
            total = sum(fl[field] for fl in self.field_lens)
            self.avg_field_len[field] = total / self.N if self.N else 1.0
        return self

    def _idf(self, term: str) -> float:
        n = self.df.get(term, 0)
        return math.log(1 + (self.N - n + 0.5) / (n + 0.5))

    def score(self, query_tokens: list[str], idx: int) -> float:
        doc_freq = self.field_freqs[idx]
        doc_len = self.field_lens[idx]
        total = 0.0
        for term in query_tokens:
            idf = self._idf(term)
            for field, weight in FIELD_WEIGHTS.items():
                tf = doc_freq[field].get(term, 0)
                if not tf:
                    continue
                dl = doc_len[field] or 1
                avg = self.avg_field_len[field] or 1
                denom = tf + self.k1 * (1 - self.b + self.b * dl / avg)
                total += weight * idf * (tf * (self.k1 + 1)) / denom
        return total


def bm25_rank(
    docs: list[dict[str, str]], query: str, top_k: int | None = None
) -> list[tuple[int, float]]:
    """Return document indices ranked by BM25, best first."""
    if not docs:
        return []
    model = BM25().fit(docs)
    tokens = tokenize(query)
    scored = [(i, model.score(tokens, i)) for i in range(len(docs))]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_k] if top_k else scored


def cosine_rank(
    query_vec: list[float], vectors: dict[int, list[float]], ids: list[int]
) -> list[tuple[int, float]]:
    """Rank ids by cosine similarity to the query vector, best first."""
    q_norm = math.sqrt(sum(x * x for x in query_vec)) or 1.0
    scored: list[tuple[int, float]] = []
    for position, item_id in enumerate(ids):
        vec = vectors.get(item_id)
        if not vec:
            continue
        dot = sum(a * b for a, b in zip(query_vec, vec))
        d_norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        scored.append((position, dot / (q_norm * d_norm)))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def rrf_fuse(
    ranked_lists: list[list[tuple[int, float]]], k: int = 60
) -> list[tuple[int, float]]:
    """Fuse any number of ranked index lists with reciprocal rank fusion."""
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, (idx, _score) in enumerate(ranked):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
    fused = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    return fused

