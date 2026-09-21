"""Corpus-level endpoints: how current the evidence base is."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.core.deps import DbPool, get_asyncpg_pool
from app.core.ratelimit import enforce_rate_limit
from app.core.security import CurrentUser
from app.services.freshness import read_freshness

router = APIRouter(prefix="/corpus", tags=["corpus"])


@router.get("/freshness")
async def corpus_freshness(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> dict[str, Any]:
    """Per-domain staleness: how many papers the source has that we don't.

    ``last_checked_at`` is null until the weekly job has run — the endpoint
    reports "unknown", never an unearned clean bill of health.
    """
    assert user.org_id is not None
    return await read_freshness(pool)
