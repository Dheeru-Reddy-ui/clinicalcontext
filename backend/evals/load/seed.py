"""Seed (and remove) the tenant a load test runs as.

Runs in its own process: the load runner imports Locust, which monkey-patches
the standard library for gevent, and asyncpg does not survive that. So the
runner asks this module for a tenant before it imports Locust, and asks it to
clean up afterwards.

    python -m evals.load.seed                → {"org_id", "user_id", "api_key"}
    python -m evals.load.seed --cleanup ORG USER
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from uuid import UUID, uuid4


async def seed() -> dict[str, str]:
    import asyncpg

    from app.config import get_settings
    from app.services.api_keys import ApiKeyService

    settings = get_settings()
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    assert pool is not None
    try:
        async with pool.acquire() as conn:
            # Enterprise: the plan whose API-key limit (3000/min) is above what
            # the ramp reaches, so the numbers measure the pipeline, not the
            # limiter. A 429 in the results is real and reported as one.
            org_id: UUID = await conn.fetchval(
                "INSERT INTO public.organizations (name, slug, plan) "
                "VALUES ($1, $2, 'enterprise') RETURNING id",
                "Load test",
                f"load-{uuid4().hex[:8]}",
            )
            user_id = uuid4()
            email = f"load-{user_id.hex[:8]}@cc-evals.test"
            await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1, $2)", user_id, email)
            await conn.execute(
                "INSERT INTO public.profiles (id, org_id, role) VALUES ($1, $2, 'owner')",
                user_id,
                org_id,
            )
        created = await ApiKeyService(pool).create(
            org_id=org_id, created_by=user_id, name="load-test", scopes=["query", "read"]
        )
    finally:
        await pool.close()
    return {"org_id": str(org_id), "user_id": str(user_id), "api_key": created.key}


async def cleanup(org_id: UUID, user_id: UUID) -> None:
    """Remove the tenant and everything it wrote (queries, answers, the cost
    ledger) so repeated runs do not accumulate load-test data."""
    import asyncpg
    from redis.asyncio import Redis

    from app.config import get_settings

    settings = get_settings()
    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute("SET session_replication_role = 'replica'")
        await conn.execute("DELETE FROM public.audit_log WHERE org_id = $1", org_id)
        await conn.execute("DELETE FROM public.answer_versions WHERE org_id = $1", org_id)
        await conn.execute("RESET session_replication_role")
        await conn.execute("DELETE FROM public.organizations WHERE id = $1", org_id)
        await conn.execute("DELETE FROM auth.users WHERE id = $1", user_id)
    finally:
        await conn.close()
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=5, socket_timeout=5)
    try:
        await redis.delete(f"semcache:{org_id}", f"rl:t:{org_id}")
    finally:
        await redis.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup", nargs=2, metavar=("ORG_ID", "USER_ID"))
    args = parser.parse_args()
    if args.cleanup:
        asyncio.run(cleanup(UUID(args.cleanup[0]), UUID(args.cleanup[1])))
        return
    json.dump(asyncio.run(seed()), sys.stdout)


if __name__ == "__main__":
    main()
