"""Embedding service: batched, retried, Redis-cached text embedding.

Wraps any :class:`TextEmbedder` (Cohere in production, the local lexical
embedder offline). The ``input_type`` distinction is preserved end to end —
chunks embed as ``search_document`` and queries as ``search_query`` — because
Cohere embeds the two asymmetrically and collapsing them quietly degrades
retrieval.

The Redis cache is keyed by a content hash *plus* the embedder identity and
input type, so a query is embedded once and switching embedders never returns
a stale vector from the wrong space.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING, Literal
from uuid import UUID

import structlog

from app.retrieval.embedders import TextEmbedder, get_embedder
from app.services import cost

if TYPE_CHECKING:
    import asyncpg
    from redis.asyncio import Redis

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("app.retrieval.embed")

InputType = Literal["search_document", "search_query"]

_COHERE_BATCH = 96
_CACHE_TTL_SECONDS = 7 * 24 * 3600
_MAX_ATTEMPTS = 4
_RETRY_BASE_DELAY_S = 1.0


class EmbeddingService:
    def __init__(
        self,
        embedder: TextEmbedder,
        redis: Redis | None = None,
        *,
        batch_size: int = _COHERE_BATCH,
        cache_ttl: int = _CACHE_TTL_SECONDS,
    ) -> None:
        self._embedder = embedder
        self._redis = redis
        self._batch_size = batch_size
        self._cache_ttl = cache_ttl

    @property
    def dimension(self) -> int:
        return self._embedder.dimension

    @property
    def embedder_name(self) -> str:
        return self._embedder.name

    def _cache_key(self, text: str, input_type: InputType) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"emb:{self._embedder.name}:{self._embedder.dimension}:{input_type[7:]}:{digest}"

    async def _call_embedder(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        """One embedder call with exponential-backoff retry on transient errors."""
        last_error: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                if input_type == "search_query":
                    return await self._embedder.embed_queries(texts)
                return await self._embedder.embed_documents(texts)
            except Exception as exc:  # provider/network errors; local embedder never raises
                last_error = exc
                if attempt == _MAX_ATTEMPTS:
                    break
                delay = _RETRY_BASE_DELAY_S * (2 ** (attempt - 1))
                logger.warning("embed_retry", attempt=attempt, delay_s=delay, error=str(exc))
                await asyncio.sleep(delay)
        raise RuntimeError(f"embedding failed after {_MAX_ATTEMPTS} attempts: {last_error}")

    async def embed_query(self, text: str) -> list[float]:
        [vector] = await self.embed_texts([text], "search_query")
        return vector

    async def embed_texts(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        """Embed with per-text caching; only cache-misses hit the embedder."""
        results: list[list[float] | None] = [None] * len(texts)
        misses: list[int] = []

        if self._redis is not None:
            keys = [self._cache_key(t, input_type) for t in texts]
            cached = await self._redis.mget(keys)
            for i, raw in enumerate(cached):
                if raw is not None:
                    results[i] = json.loads(raw)
                else:
                    misses.append(i)
        else:
            misses = list(range(len(texts)))

        hits = [i for i in range(len(texts)) if i not in set(misses)]
        if hits:
            cost.record(
                "embedding",
                provider=_provider_of(self._embedder.name),
                model=self._embedder.name,
                units=sum(_approx_tokens(texts[i]) for i in hits),
                unit="tokens",
                cached=True,
            )
        for start in range(0, len(misses), self._batch_size):
            batch_indices = misses[start : start + self._batch_size]
            vectors = await self._call_embedder([texts[i] for i in batch_indices], input_type)
            cost.record(
                "embedding",
                provider=_provider_of(self._embedder.name),
                model=self._embedder.name,
                units=sum(_approx_tokens(texts[i]) for i in batch_indices),
                unit="tokens",
            )
            for index, vector in zip(batch_indices, vectors, strict=True):
                results[index] = vector
            if self._redis is not None:
                pipe = self._redis.pipeline()
                for index, vector in zip(batch_indices, vectors, strict=True):
                    pipe.set(
                        self._cache_key(texts[index], input_type),
                        json.dumps(vector),
                        ex=self._cache_ttl,
                    )
                await pipe.execute()

        return [vector for vector in results if vector is not None]


def _approx_tokens(text: str) -> int:
    """Cohere bills tokens; ~4 characters each for English biomedical prose."""
    return max(1, round(len(text) / 4))


def _provider_of(model: str) -> str:
    return "cohere" if model.startswith("cohere") or model.startswith("embed") else "local"


async def embed_document_chunks(
    pool: DbPool,
    document_id: UUID,
    *,
    embedder_name: str = "cohere",
    redis: Redis | None = None,
    batch_size: int = _COHERE_BATCH,
) -> int:
    """Embed the pending chunks of ONE document. Returns the number embedded.

    Used by the private-upload path, which must make the tenant's new document
    searchable immediately without dragging the whole corpus backlog along.
    """
    from app.repositories.corpus import CorpusRepository

    service = EmbeddingService(get_embedder(embedder_name), redis, batch_size=batch_size)
    repo = CorpusRepository()
    async with pool.acquire() as conn:
        rows = await repo.fetch_document_chunks_pending_embedding(conn, document_id=document_id)
        if not rows:
            return 0
        vectors = await service.embed_texts([str(r["text"]) for r in rows], "search_document")
        await repo.write_embeddings(
            conn,
            [
                (UUID(str(row["id"])), row["org_id"], str(row["strategy"]), vector)
                for row, vector in zip(rows, vectors, strict=True)
            ],
        )
    return len(rows)


async def backfill_pending_chunks(
    pool: DbPool,
    *,
    embedder_name: str = "cohere",
    redis: Redis | None = None,
    limit: int | None = None,
    batch_size: int = _COHERE_BATCH,
) -> int:
    """Embed chunks where embedding IS NULL. Returns the number embedded.

    Embeds ``coalesce(embed_text, content)`` so context-enriched strategies
    (structural) embed the augmented text while the stored passage stays clean.
    Provider auth errors propagate untouched — callers decide fatal vs deferrable.
    """
    from app.repositories.corpus import CorpusRepository

    service = EmbeddingService(get_embedder(embedder_name), redis, batch_size=batch_size)
    repo = CorpusRepository()
    embedded = 0
    async with pool.acquire() as conn:
        while True:
            remaining = batch_size if limit is None else min(batch_size, limit - embedded)
            if remaining <= 0:
                break
            rows = await repo.fetch_chunks_pending_embedding(conn, limit=remaining)
            if not rows:
                break
            vectors = await service.embed_texts([str(r["text"]) for r in rows], "search_document")
            pairs: list[tuple[UUID, UUID | None, str, list[float]]] = [
                (UUID(str(row["id"])), row["org_id"], str(row["strategy"]), vector)
                for row, vector in zip(rows, vectors, strict=True)
            ]
            await repo.write_embeddings(conn, pairs)
            embedded += len(pairs)
            logger.info("embedding_progress", embedded=embedded, embedder=embedder_name)
    return embedded
