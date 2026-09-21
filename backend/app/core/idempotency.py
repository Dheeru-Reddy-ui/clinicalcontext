"""Idempotency-Key support (Redis-backed, 24h).

A ``POST /v1/queries`` carrying an ``Idempotency-Key`` header records its
terminal result under the key (scoped to the tenant). A replay within the TTL
returns the stored events verbatim — no second guardrail/graph run — which is
how a real API behaves under client retries. Keys are tenant-scoped so one
tenant's key can never surface another's response.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from redis.asyncio import Redis

_TTL_SECONDS = 24 * 3600


class IdempotencyStore:
    def __init__(self, redis: Redis, *, ttl_seconds: int = _TTL_SECONDS) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    def _key(self, org_id: UUID, idem_key: str) -> str:
        return f"idem:{org_id}:{idem_key}"

    async def get(self, org_id: UUID, idem_key: str) -> list[dict[str, Any]] | None:
        raw = await self._redis.get(self._key(org_id, idem_key))
        if raw is None:
            return None
        events: list[dict[str, Any]] = json.loads(raw)
        return events

    async def put(self, org_id: UUID, idem_key: str, events: list[dict[str, Any]]) -> None:
        # NX: never overwrite a completed run under the same key.
        await self._redis.set(
            self._key(org_id, idem_key), json.dumps(events), ex=self._ttl, nx=True
        )
