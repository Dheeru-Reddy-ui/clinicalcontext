"""Analytics endpoints — what this tenant asked, what it cost, how good it was."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbPool, get_asyncpg_pool
from app.core.ratelimit import enforce_rate_limit
from app.core.security import CurrentUser
from app.schemas.analytics import CostReport, QualityReport, UsageOverview, UsageSeries
from app.services.analytics import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])

Days = Annotated[int, Query(ge=1, le=365, description="Look-back window in days")]


@router.get("/overview")
async def overview(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    days: Days = 30,
) -> UsageOverview:
    """Headline numbers: volume, abstention, cache hits, cost, latency."""
    assert user.org_id is not None
    return await AnalyticsService(pool).overview(
        org_id=user.org_id, user_id=user.user_id, days=days
    )


@router.get("/usage")
async def usage(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    days: Days = 30,
) -> UsageSeries:
    """Daily query volume and spend (zero-filled, chart-ready)."""
    assert user.org_id is not None
    return await AnalyticsService(pool).usage_series(
        org_id=user.org_id, user_id=user.user_id, days=days
    )


@router.get("/quality")
async def quality(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    days: Days = 30,
    top: Annotated[int, Query(ge=1, le=50)] = 10,
) -> QualityReport:
    """Answer quality: abstention, contradictions, confidence mix, feedback."""
    assert user.org_id is not None
    return await AnalyticsService(pool).quality(
        org_id=user.org_id, user_id=user.user_id, days=days, top=top
    )


@router.get("/cost")
async def cost_report(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    days: Days = 30,
) -> CostReport:
    """Real spend per component (embedding, rerank, generation, STT, TTS)
    from the cost ledger, with what the caches avoided and — on the offline
    backend — what the same usage would cost at cloud list prices."""
    assert user.org_id is not None
    return await AnalyticsService(pool).cost(org_id=user.org_id, user_id=user.user_id, days=days)
