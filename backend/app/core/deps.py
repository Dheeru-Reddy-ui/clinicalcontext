"""FastAPI dependencies for shared infrastructure resources.

Resources live on ``app.state`` (created in the lifespan). Handlers depend on
the *protocols* below rather than concrete clients, which keeps routes
testable with in-memory fakes and keeps vendor types out of the API layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import asyncpg
from redis.asyncio import Redis
from starlette.requests import Request

from app.core.errors import ServiceUnavailableError

if TYPE_CHECKING:
    # The stubs make Pool generic; the runtime class is not subscriptable.
    # FastAPI evaluates annotations at runtime, so signatures it introspects
    # must use this alias, never asyncpg.Pool[...] directly.
    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    DbPool = asyncpg.Pool


@runtime_checkable
class DatabasePool(Protocol):
    """The minimal asyncpg.Pool surface the app depends on."""

    async def fetchval(self, query: str) -> Any: ...


@runtime_checkable
class RedisClient(Protocol):
    """The minimal redis client surface the app depends on."""

    async def ping(self) -> Any: ...


def get_db_pool(request: Request) -> DatabasePool | None:
    """Return the process database pool, or None if it failed to initialize."""
    pool = getattr(request.app.state, "db_pool", None)
    return pool if isinstance(pool, DatabasePool) else None


def get_redis(request: Request) -> RedisClient | None:
    """Return the process redis client, or None if it failed to initialize."""
    client = getattr(request.app.state, "redis", None)
    return client if isinstance(client, RedisClient) else None


def get_asyncpg_pool(request: Request) -> DbPool:
    """The concrete database pool for request handling.

    Unlike the health-check accessors above, routes cannot do useful work
    without the database — a missing pool is a 503, not a None.
    """
    pool = getattr(request.app.state, "db_pool", None)
    if not isinstance(pool, asyncpg.Pool):
        raise ServiceUnavailableError("database is unavailable")
    return pool


def get_redis_client(request: Request) -> Redis:
    """The concrete Redis client (rate limiting, semantic cache, idempotency)."""
    client = getattr(request.app.state, "redis", None)
    if not isinstance(client, Redis):
        raise ServiceUnavailableError("cache/redis is unavailable")
    return client
