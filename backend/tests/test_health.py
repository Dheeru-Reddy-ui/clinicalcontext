"""Liveness and readiness endpoints, with dependency fakes."""

from __future__ import annotations

from fastapi import FastAPI
from httpx import AsyncClient

from app import __version__


class FakePool:
    async def fetchval(self, query: str) -> int:
        assert query == "SELECT 1"
        return 1


class BrokenPool:
    async def fetchval(self, query: str) -> int:
        raise ConnectionError("database is down")


class FakeRedis:
    async def ping(self) -> bool:
        return True


class BrokenRedis:
    async def ping(self) -> bool:
        raise ConnectionError("redis is down")


async def test_health_is_alive_without_dependencies(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "clinicalcontext-backend"
    assert body["version"] == __version__


async def test_ready_ok_when_all_dependencies_up(test_app: FastAPI, client: AsyncClient) -> None:
    test_app.state.db_pool = FakePool()
    test_app.state.redis = FakeRedis()

    response = await client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["redis"]["status"] == "ok"
    assert body["checks"]["database"]["latency_ms"] is not None


async def test_ready_503_when_database_down(test_app: FastAPI, client: AsyncClient) -> None:
    test_app.state.db_pool = BrokenPool()
    test_app.state.redis = FakeRedis()

    response = await client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"]["status"] == "error"
    assert "ConnectionError" in body["checks"]["database"]["detail"]
    assert body["checks"]["redis"]["status"] == "ok"


async def test_ready_503_when_redis_down(test_app: FastAPI, client: AsyncClient) -> None:
    test_app.state.db_pool = FakePool()
    test_app.state.redis = BrokenRedis()

    response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["redis"]["status"] == "error"


async def test_ready_503_when_nothing_initialized(client: AsyncClient) -> None:
    response = await client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["database"]["detail"] == "database pool is not initialized"
    assert body["checks"]["redis"]["detail"] == "redis client is not initialized"


async def test_probes_answer_HEAD_as_well_as_GET(client: AsyncClient) -> None:
    """Load balancers and uptime monitors commonly probe with HEAD.

    A probe endpoint that answers 405 to HEAD reads as an outage to whatever
    is watching it, which is the opposite of the endpoint's job.
    """
    for path in ("/health", "/ready"):
        head = await client.head(path)
        get = await client.get(path)
        assert head.status_code == get.status_code, path
        assert head.status_code != 405, path
