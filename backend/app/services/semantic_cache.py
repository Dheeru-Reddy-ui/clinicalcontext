"""Per-tenant semantic answer cache.

Before running the graph, embed the query and compare it (cosine) against
recent queries for the *same tenant*. On a hit above the threshold, the prior
answer is returned with ``cached=True`` and the provider cost it saved is
recorded, so the dashboard can show the saving. Strictly tenant-scoped: the
Redis key includes the org id, so one tenant never sees another's cache.

An answer is only as good as what wrote it, so the key also names the
writer — the reasoner and ANSWER_FORMAT_VERSION. Without that, an answer
quoted from passages while no language model was configured kept being
served for a week after one was, and every change to how answers are built
waited out the old entries. Bump the version whenever that changes.
"""

from __future__ import annotations

import json
import math
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import structlog

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = structlog.stdlib.get_logger("app.services.semantic_cache")

SIMILARITY_THRESHOLD = 0.97
_MAX_ENTRIES = 200
_TTL_SECONDS = 7 * 24 * 3600

#: 2 — off-topic passages are kept out of answers and out of the
#: contradiction check; extractive answers quote each source's finding.
ANSWER_FORMAT_VERSION = 2


def cache_namespace(writer: str) -> str:
    """The key segment for answers written by ``writer`` in today's format."""
    return f"{writer}.v{ANSWER_FORMAT_VERSION}"


async def clear_semantic_cache(redis: Redis, org_id: UUID) -> None:
    """Drop every cached answer for one tenant, whichever writer made it."""
    keys = [key async for key in redis.scan_iter(match=f"semcache:{org_id}*", count=100)]
    if keys:
        await redis.delete(*keys)


@dataclass(slots=True)
class CacheHit:
    similarity: float
    payload: dict[str, Any]
    saved_usd: float
    # The provider calls the stored answer took (rerank, generation), replayed
    # into the cost ledger as avoided units when this entry serves a hit.
    ledger: list[dict[str, Any]] = field(default_factory=list)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class SemanticCache:
    def __init__(
        self,
        redis: Redis,
        *,
        namespace: str = "default",
        threshold: float = SIMILARITY_THRESHOLD,
    ) -> None:
        self._redis = redis
        self._namespace = namespace
        self._threshold = threshold

    def _key(self, org_id: UUID) -> str:
        return f"semcache:{org_id}:{self._namespace}"

    async def lookup(self, org_id: UUID, query_vector: list[float]) -> CacheHit | None:
        # redis-py types list commands as ``Awaitable[T] | T``; on the async
        # client they are always awaitable, so cast before awaiting (mypy strict).
        raw_entries: list[Any] = await cast(
            "Awaitable[list[Any]]", self._redis.lrange(self._key(org_id), 0, _MAX_ENTRIES - 1)
        )
        best: CacheHit | None = None
        for raw in raw_entries:
            entry = json.loads(raw)
            similarity = _cosine(query_vector, entry["embedding"])
            if similarity >= self._threshold and (best is None or similarity > best.similarity):
                best = CacheHit(
                    similarity=round(similarity, 4),
                    payload=entry["payload"],
                    saved_usd=float(entry.get("cost_usd", 0.0)),
                    ledger=list(entry.get("ledger", [])),
                )
        if best is not None:
            logger.info("semantic_cache_hit", org_id=str(org_id), similarity=best.similarity)
        return best

    async def store(
        self,
        org_id: UUID,
        query_vector: list[float],
        payload: dict[str, Any],
        cost_usd: float,
        ledger: list[dict[str, Any]] | None = None,
    ) -> None:
        entry = json.dumps(
            {
                "embedding": query_vector,
                "payload": payload,
                "cost_usd": cost_usd,
                "ledger": ledger or [],
            }
        )
        key = self._key(org_id)
        await cast("Awaitable[int]", self._redis.lpush(key, entry))
        await cast("Awaitable[str]", self._redis.ltrim(key, 0, _MAX_ENTRIES - 1))
        await self._redis.expire(key, _TTL_SECONDS)
