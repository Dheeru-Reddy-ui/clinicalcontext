"""Query autocomplete."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbPool, get_asyncpg_pool
from app.core.ratelimit import enforce_rate_limit
from app.core.security import CurrentUser
from app.schemas.suggest import SuggestOut
from app.services.suggest import SuggestService

router = APIRouter(prefix="/suggest", tags=["suggest"])


@router.get("")
async def suggest(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    q: Annotated[str, Query(min_length=0, max_length=200, description="Partial query")] = "",
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
) -> SuggestOut:
    """Type-ahead over MeSH vocabulary and this org's own query history."""
    assert user.org_id is not None
    return await SuggestService(pool).suggest(
        org_id=user.org_id, user_id=user.user_id, query=q, limit=limit
    )
