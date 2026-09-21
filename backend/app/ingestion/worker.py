"""arq worker for background ingestion jobs.

Run with:  uv run arq app.ingestion.worker.WorkerSettings

Progress is published to Redis under ingest:progress:{job_id}; failures retry
with exponential backoff (10s, 20s, 40s) and dead-letter into
ingestion_failures on the final attempt.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

import asyncpg
import structlog
from arq import Retry
from arq.connections import RedisSettings

from app.config import get_settings
from app.ingestion.models import IngestStats
from app.ingestion.pipeline import ingest_from_source
from app.repositories.corpus import CorpusRepository

if TYPE_CHECKING:
    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("app.ingestion.worker")

MAX_TRIES = 4
_PROGRESS_TTL_S = 86_400


async def ingest_job(ctx: dict[str, Any], *, source: str, query: str, limit: int) -> dict[str, Any]:
    pool: DbPool = ctx["pool"]
    job_id = str(ctx.get("job_id", "unknown"))
    job_try = int(ctx.get("job_try", 1))
    progress_key = f"ingest:progress:{job_id}"

    async def publish_progress(stats: IngestStats) -> None:
        await ctx["redis"].set(progress_key, json.dumps(stats.as_dict()), ex=_PROGRESS_TTL_S)

    try:
        stats = await ingest_from_source(
            pool,
            source=source,
            query=query,
            limit=limit,
            progress_cb=publish_progress,
        )
    except Exception as exc:
        logger.error(
            "ingest_job_failed",
            job_id=job_id,
            job_try=job_try,
            error=f"{type(exc).__name__}: {exc}",
        )
        if job_try >= MAX_TRIES:
            async with pool.acquire() as conn:
                await CorpusRepository().record_failure(
                    conn,
                    source=source,
                    external_id=None,
                    stage="job",
                    error=f"{type(exc).__name__}: {exc}",
                    payload={"query": query, "limit": limit, "job_id": job_id},
                )
            raise
        raise Retry(defer=10 * 2 ** (job_try - 1)) from exc

    await publish_progress(stats)
    return stats.as_dict()


async def startup(ctx: dict[str, Any]) -> None:
    ctx["pool"] = await asyncpg.create_pool(
        dsn=get_settings().database_url,
        min_size=1,
        max_size=5,
        server_settings={"search_path": "public, extensions"},
    )
    logger.info("worker_started")


async def shutdown(ctx: dict[str, Any]) -> None:
    pool: DbPool | None = ctx.get("pool")
    if pool is not None:
        await pool.close()
    logger.info("worker_stopped")


class WorkerSettings:
    """arq entrypoint. Resolving settings here means the module (and the
    process) fails fast at import when the environment is incomplete."""

    functions: ClassVar[list[Callable[..., Any]]] = [ingest_job]
    on_startup = startup
    on_shutdown = shutdown
    max_tries = MAX_TRIES
    job_timeout = 3600
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
