"""Batch query jobs: submit many questions, poll (or get a webhook) for results.

The submit path only writes rows and returns a job id — the questions are
answered afterwards, one at a time, through the very same ``AskService`` a
single query uses. Sharing that path is deliberate: batch answers get the same
guardrails, grounding, cache and cost accounting as interactive ones, so a
batch can never become a quiet back door around them.

Progress is recorded per item as it completes, so polling reports real
progress rather than flipping from queued to done.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from app.repositories.base import tenant_connection
from app.services.ask import AskService
from app.services.webhooks import enqueue_event

if TYPE_CHECKING:
    import asyncpg
    from redis.asyncio import Redis

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("app.services.batch")

MAX_BATCH_SIZE = 50


async def create_batch(pool: DbPool, *, org_id: UUID, user_id: UUID, queries: list[str]) -> UUID:
    """Record the job and its items atomically; nothing is run yet."""
    async with tenant_connection(pool, org_id, user_id) as conn, conn.transaction():
        batch_id = await conn.fetchval(
            "INSERT INTO public.batch_jobs (org_id, user_id, total) VALUES ($1, $2, $3) "
            "RETURNING id",
            org_id,
            user_id,
            len(queries),
        )
        await conn.executemany(
            "INSERT INTO public.batch_items (batch_id, org_id, position, query) "
            "VALUES ($1, $2, $3, $4)",
            [(batch_id, org_id, i, q) for i, q in enumerate(queries)],
        )
    return UUID(str(batch_id))


async def get_batch(pool: DbPool, *, org_id: UUID, user_id: UUID, batch_id: UUID) -> Any:
    from app.core.errors import NotFoundError

    async with tenant_connection(pool, org_id, user_id) as conn:
        job = await conn.fetchrow(
            "SELECT id, status, total, completed, failed, created_at, finished_at "
            "FROM public.batch_jobs WHERE id = $1",
            batch_id,
        )
        if job is None:
            raise NotFoundError("batch job not found")
        items = await conn.fetch(
            """
            SELECT b.position, b.query, b.status, b.query_id, b.answer_id, b.error,
                   a.content, a.confidence, a.abstained
            FROM public.batch_items b
            LEFT JOIN public.answers a ON a.id = b.answer_id
            WHERE b.batch_id = $1
            ORDER BY b.position
            """,
            batch_id,
        )
    return {
        "id": str(job["id"]),
        "status": job["status"],
        "total": job["total"],
        "completed": job["completed"],
        "failed": job["failed"],
        "created_at": job["created_at"].isoformat(),
        "finished_at": job["finished_at"].isoformat() if job["finished_at"] else None,
        "items": [
            {
                "position": i["position"],
                "query": i["query"],
                "status": i["status"],
                "query_id": str(i["query_id"]) if i["query_id"] else None,
                "answer_id": str(i["answer_id"]) if i["answer_id"] else None,
                "answer": i["content"],
                "confidence": i["confidence"],
                "abstained": i["abstained"],
                "error": i["error"],
            }
            for i in items
        ],
    }


async def process_batch(
    pool: DbPool,
    redis: Redis | None,
    *,
    org_id: UUID,
    user_id: UUID,
    batch_id: UUID,
) -> None:
    """Answer every queued item, recording progress as it goes.

    Idempotent at the item level: only ``queued`` items are picked up, so a
    re-run after a crash resumes rather than duplicating work.
    """
    service = AskService(pool, redis)
    async with tenant_connection(pool, org_id, user_id) as conn:
        await conn.execute(
            "UPDATE public.batch_jobs SET status = 'running' WHERE id = $1 AND status = 'queued'",
            batch_id,
        )
        # An item left 'running' belongs to a previous attempt that died before
        # recording an outcome; requeue it so a resumed run actually resumes.
        await conn.execute(
            "UPDATE public.batch_items SET status = 'queued' "
            "WHERE batch_id = $1 AND status = 'running'",
            batch_id,
        )
        pending = await conn.fetch(
            "SELECT id, query FROM public.batch_items "
            "WHERE batch_id = $1 AND status = 'queued' ORDER BY position",
            batch_id,
        )

    for item in pending:
        item_id = item["id"]
        async with tenant_connection(pool, org_id, user_id) as conn:
            await conn.execute(
                "UPDATE public.batch_items SET status = 'running' WHERE id = $1", item_id
            )
        status, query_id, answer_id, error = await _run_one(
            service, org_id=org_id, user_id=user_id, query=item["query"]
        )
        async with tenant_connection(pool, org_id, user_id) as conn, conn.transaction():
            await conn.execute(
                "UPDATE public.batch_items SET status = $2, query_id = $3, answer_id = $4, "
                "error = $5 WHERE id = $1",
                item_id,
                status,
                query_id,
                answer_id,
                error,
            )
            column = "failed" if status == "failed" else "completed"
            await conn.execute(
                f"UPDATE public.batch_jobs SET {column} = {column} + 1 WHERE id = $1",
                batch_id,
            )

    async with tenant_connection(pool, org_id, user_id) as conn, conn.transaction():
        job = await conn.fetchrow(
            "UPDATE public.batch_jobs SET status = 'completed', finished_at = now() "
            "WHERE id = $1 RETURNING total, completed, failed",
            batch_id,
        )
        assert job is not None
        await enqueue_event(
            conn,
            org_id=org_id,
            event="batch.completed",
            payload={
                "batch_id": str(batch_id),
                "total": job["total"],
                "completed": job["completed"],
                "failed": job["failed"],
            },
        )
    logger.info("batch_completed", batch_id=str(batch_id), total=job["total"])


async def _run_one(
    service: AskService, *, org_id: UUID, user_id: UUID, query: str
) -> tuple[str, UUID | None, UUID | None, str | None]:
    """Drive one question to its terminal event. Never raises."""
    query_id: UUID | None = None
    answer_id: UUID | None = None
    try:
        async for event in service.ask(query=query, org_id=org_id, user_id=user_id):
            data = event.get("data", {})
            if event["stage"] == "accepted":
                query_id = UUID(str(data["query_id"]))
            elif event["stage"] == "blocked":
                return "blocked", query_id, None, str(event.get("message", "blocked"))
            elif event["stage"] == "result":
                answer_id = UUID(str(data["answer_id"])) if data.get("answer_id") else None
        return "completed", query_id, answer_id, None
    except Exception as exc:
        # One bad question must not abort the rest of the batch.
        logger.error("batch_item_failed", error=f"{type(exc).__name__}: {exc}")
        return "failed", query_id, answer_id, f"{type(exc).__name__}: {exc}"


def spawn_batch(
    pool: DbPool, redis: Redis | None, *, org_id: UUID, user_id: UUID, batch_id: UUID
) -> asyncio.Task[None]:
    """Run the batch in the background of this process.

    Items stay ``queued`` until picked up, so a restart mid-batch leaves the
    job resumable by re-invoking ``process_batch`` rather than losing it.
    """

    async def runner() -> None:
        try:
            await process_batch(pool, redis, org_id=org_id, user_id=user_id, batch_id=batch_id)
        except Exception as exc:
            logger.error(
                "batch_run_failed", batch_id=str(batch_id), error=f"{type(exc).__name__}: {exc}"
            )
            async with tenant_connection(pool, org_id, user_id) as conn:
                await conn.execute(
                    "UPDATE public.batch_jobs SET status = 'failed', finished_at = now() "
                    "WHERE id = $1",
                    batch_id,
                )

    return asyncio.create_task(runner())
