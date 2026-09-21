"""Reranking — reorder the fused candidates by direct query↔passage relevance.

Two implementations behind one protocol:

- :class:`CohereReranker` — Cohere ``rerank-v3.5``, a cross-encoder that reads
  each passage against the query (the production reranker).
- :class:`BM25Reranker` — an offline BM25 scorer computed over the candidate
  set's own statistics. A genuinely different signal from embedding cosine and
  Postgres ``ts_rank``, so the pipeline and ablation run and reorder without a
  paid key.

Both return the top-N with a ``rerank`` score recorded in ``components``.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Protocol, runtime_checkable

import structlog

from app.retrieval.types import RetrievedChunk

logger = structlog.stdlib.get_logger("app.retrieval.rerank")

RERANK_MODEL = "rerank-v3.5"
_WORD = re.compile(r"[a-z0-9]+")
_BM25_K1 = 1.5
_BM25_B = 0.75


@runtime_checkable
class Reranker(Protocol):
    name: str

    async def rerank(
        self, query: str, chunks: list[RetrievedChunk], *, top_n: int
    ) -> list[RetrievedChunk]: ...


class BM25Reranker:
    """Okapi BM25 over the candidate passages (IDF from the candidates)."""

    name = "bm25-local"

    async def rerank(
        self, query: str, chunks: list[RetrievedChunk], *, top_n: int
    ) -> list[RetrievedChunk]:
        if not chunks:
            return []
        docs_tokens = [_WORD.findall(chunk.content.lower()) for chunk in chunks]
        doc_lengths = [len(tokens) for tokens in docs_tokens]
        avg_len = (sum(doc_lengths) / len(doc_lengths)) or 1.0
        n_docs = len(chunks)

        # Document frequency across the candidate set.
        doc_freq: Counter[str] = Counter()
        for tokens in docs_tokens:
            doc_freq.update(set(tokens))

        query_terms = _WORD.findall(query.lower())
        scored: list[RetrievedChunk] = []
        for chunk, tokens, length in zip(chunks, docs_tokens, doc_lengths, strict=True):
            term_counts = Counter(tokens)
            score = 0.0
            for term in query_terms:
                if term not in term_counts:
                    continue
                idf = math.log(1.0 + (n_docs - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
                tf = term_counts[term]
                denominator = tf + _BM25_K1 * (1.0 - _BM25_B + _BM25_B * length / avg_len)
                score += idf * (tf * (_BM25_K1 + 1.0)) / denominator
            scored.append(chunk.with_score("rerank", score))
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:top_n]


class CohereReranker:
    """Cohere rerank-v3.5 (production cross-encoder reranker)."""

    name = "cohere-rerank-v3.5"

    def __init__(self) -> None:
        from cohere import AsyncClientV2

        from app.config import get_settings

        self._client = AsyncClientV2(api_key=get_settings().cohere_api_key.get_secret_value())

    async def rerank(
        self, query: str, chunks: list[RetrievedChunk], *, top_n: int
    ) -> list[RetrievedChunk]:
        if not chunks:
            return []
        from app.core.telemetry import span

        with span("provider.cohere.rerank", model=RERANK_MODEL, documents=len(chunks), top_n=top_n):
            response = await self._client.rerank(
                model=RERANK_MODEL,
                query=query,
                documents=[chunk.content for chunk in chunks],
                top_n=min(top_n, len(chunks)),
            )
        reranked: list[RetrievedChunk] = []
        for result in response.results:
            chunk = chunks[result.index]
            reranked.append(chunk.with_score("rerank", float(result.relevance_score)))
        return reranked


def get_reranker(name: str) -> Reranker:
    if name in ("local", "bm25", "bm25-local"):
        return BM25Reranker()
    if name in ("cohere", "cohere-rerank-v3.5"):
        return CohereReranker()
    raise ValueError(f"unknown reranker: {name!r}")
