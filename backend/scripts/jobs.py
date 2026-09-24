"""Scheduled maintenance jobs.

These are the out-of-band workers the API deliberately does *not* run inline:

    uv run python -m scripts.jobs deliver-webhooks      # every minute
    uv run python -m scripts.jobs living-answers        # nightly
    uv run python -m scripts.jobs corpus-freshness      # weekly (needs network)
    uv run python -m scripts.jobs rebuild-mesh          # after an ingest
    uv run python -m scripts.jobs weekly-digest         # weekly (opt-in users)
    uv run python -m scripts.jobs latest-research       # nightly (needs network)

Each is idempotent and safe to re-run: deliveries are picked up by due time,
living answers only supersede on a material change, freshness upserts by
domain, and the MeSH rebuild is a full replace.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import asyncpg
import structlog

from app.config import get_settings
from app.core.logging import setup_logging

if TYPE_CHECKING:
    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("scripts.jobs")

Summary = dict[str, int]


async def _with_pool(runner: Callable[[DbPool], Awaitable[Summary]]) -> Summary:
    """Open a pool, run one job against it, and always close it again."""
    pool = await asyncpg.create_pool(
        dsn=get_settings().database_url,
        min_size=1,
        max_size=5,
        server_settings={"search_path": "public, extensions"},
    )
    assert pool is not None  # create_pool only returns None in lazy mode
    try:
        return await runner(pool)
    finally:
        await pool.close()


async def _deliver_webhooks(limit: int) -> Summary:
    from app.services.webhooks import deliver_pending

    async def run(pool: DbPool) -> Summary:
        return await deliver_pending(pool, limit=limit)

    return await _with_pool(run)


async def _living_answers(limit: int) -> Summary:
    from redis.asyncio import Redis

    from app.services.living_answers import refresh_followed_answers

    redis: Redis = Redis.from_url(
        get_settings().redis_url, socket_connect_timeout=5, socket_timeout=5
    )

    async def run(pool: DbPool) -> Summary:
        return await refresh_followed_answers(pool, redis, limit=limit)

    try:
        return await _with_pool(run)
    finally:
        await redis.aclose()


async def _corpus_freshness(per_query: int) -> Summary:
    from app.services.freshness import refresh_corpus_freshness

    async def run(pool: DbPool) -> Summary:
        results = await refresh_corpus_freshness(pool, per_query=per_query)
        return {
            "domains": len(results),
            "stale": sum(1 for r in results if r.stale),
            "missing": sum(r.source_new_count for r in results),
        }

    return await _with_pool(run)


async def _weekly_digest(force: bool) -> Summary:
    from app.services.digest import run_weekly_digest

    async def run(pool: DbPool) -> Summary:
        return await run_weekly_digest(pool, force=force)

    return await _with_pool(run)


async def _rebuild_mesh() -> Summary:
    from app.services.suggest import rebuild_mesh_terms

    async def run(pool: DbPool) -> Summary:
        return {"terms": await rebuild_mesh_terms(pool)}

    return await _with_pool(run)


async def _latest_research(per_specialty: int, days: int) -> Summary:
    from app.knowledge.refresh import refresh_latest

    async def run(pool: DbPool) -> Summary:
        return await refresh_latest(pool, per_specialty=per_specialty, days=days)

    return await _with_pool(run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="job", required=True)

    deliver = sub.add_parser("deliver-webhooks", help="drain the webhook delivery queue")
    deliver.add_argument("--limit", type=int, default=50)

    living = sub.add_parser("living-answers", help="re-check followed answers")
    living.add_argument("--limit", type=int, default=200)

    freshness = sub.add_parser("corpus-freshness", help="re-query each domain (needs network)")
    freshness.add_argument("--per-query", type=int, default=50)

    sub.add_parser("rebuild-mesh", help="rebuild the autocomplete vocabulary")

    latest = sub.add_parser(
        "latest-research", help="file each specialty's newest strong evidence (needs network)"
    )
    latest.add_argument("--per-specialty", type=int, default=3)
    latest.add_argument("--days", type=int, default=30)

    digest = sub.add_parser(
        "weekly-digest", help="send the weekly evidence digest to opted-in users"
    )
    digest.add_argument("--force", action="store_true", help="ignore the six-day minimum interval")

    args = parser.parse_args(argv)
    setup_logging(get_settings().log_level)

    try:
        if args.job == "deliver-webhooks":
            summary: dict[str, int] = asyncio.run(_deliver_webhooks(args.limit))
        elif args.job == "living-answers":
            summary = asyncio.run(_living_answers(args.limit))
        elif args.job == "corpus-freshness":
            summary = asyncio.run(_corpus_freshness(args.per_query))
        elif args.job == "weekly-digest":
            summary = asyncio.run(_weekly_digest(args.force))
        elif args.job == "latest-research":
            summary = asyncio.run(_latest_research(args.per_specialty, args.days))
        else:
            summary = asyncio.run(_rebuild_mesh())
    except (asyncpg.PostgresError, OSError) as exc:
        print(f"job failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    logger.info("job_complete", job=args.job, **summary)
    print(f"{args.job}: " + ", ".join(f"{k}={v}" for k, v in summary.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
