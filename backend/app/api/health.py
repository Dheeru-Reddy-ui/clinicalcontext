"""Liveness (`/health`) and readiness (`/ready`) endpoints.

`/health` answers "is the process up"; `/ready` answers "can it do real work"
by probing Postgres and Redis with a short timeout. Readiness never raises —
a broken dependency yields a 503 with a per-check breakdown, not a stack trace.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Response
from starlette import status
from starlette.requests import Request

from app import __version__
from app.core.deps import DatabasePool, RedisClient, get_db_pool, get_redis
from app.schemas.health import HealthResponse, ReadinessCheck, ReadinessResponse

router = APIRouter(tags=["health"])

_CHECK_TIMEOUT_S = 3.0


@router.api_route("/health", methods=["GET", "HEAD"])
async def health() -> HealthResponse:
    return HealthResponse(version=__version__)


async def _check_database(pool: DatabasePool | None) -> ReadinessCheck:
    if pool is None:
        return ReadinessCheck(status="error", detail="database pool is not initialized")
    start = time.perf_counter()
    try:
        value = await asyncio.wait_for(pool.fetchval("SELECT 1"), timeout=_CHECK_TIMEOUT_S)
    except Exception as exc:
        return ReadinessCheck(status="error", detail=f"{type(exc).__name__}: {exc}")
    if value != 1:
        return ReadinessCheck(status="error", detail="unexpected result for SELECT 1")
    return ReadinessCheck(status="ok", latency_ms=round((time.perf_counter() - start) * 1000, 2))


async def _check_redis(client: RedisClient | None) -> ReadinessCheck:
    if client is None:
        return ReadinessCheck(status="error", detail="redis client is not initialized")
    start = time.perf_counter()
    try:
        await asyncio.wait_for(client.ping(), timeout=_CHECK_TIMEOUT_S)
    except Exception as exc:
        return ReadinessCheck(status="error", detail=f"{type(exc).__name__}: {exc}")
    return ReadinessCheck(status="ok", latency_ms=round((time.perf_counter() - start) * 1000, 2))


@router.api_route("/ready", methods=["GET", "HEAD"])
async def ready(request: Request, response: Response) -> ReadinessResponse:
    database_check, redis_check = await asyncio.gather(
        _check_database(get_db_pool(request)),
        _check_redis(get_redis(request)),
    )
    checks = {"database": database_check, "redis": redis_check}
    all_ok = all(check.status == "ok" for check in checks.values())
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ok" if all_ok else "degraded", checks=checks)
