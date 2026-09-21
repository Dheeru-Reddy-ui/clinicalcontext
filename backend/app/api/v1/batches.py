"""Batch query jobs: submit up to 50 questions, poll or receive a webhook."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.deps import DbPool, get_asyncpg_pool, get_redis_client
from app.core.errors import PermissionDeniedError
from app.core.ratelimit import enforce_rate_limit
from app.core.security import CurrentUser
from app.schemas.query import BatchQueryRequest
from app.services.batch import create_batch, get_batch, spawn_batch

router = APIRouter(prefix="/batches", tags=["batches"])


@router.post("", status_code=202)
async def submit_batch(
    body: BatchQueryRequest,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    redis: Annotated[Any, Depends(get_redis_client)],
) -> dict[str, Any]:
    """Queue a batch and return its job id immediately (202 Accepted)."""
    if user.role == "viewer":
        raise PermissionDeniedError("this principal has read-only access and cannot run queries")
    assert user.org_id is not None
    batch_id = await create_batch(
        pool, org_id=user.org_id, user_id=user.user_id, queries=body.queries
    )
    spawn_batch(pool, redis, org_id=user.org_id, user_id=user.user_id, batch_id=batch_id)
    return {
        "batch_id": str(batch_id),
        "status": "queued",
        "total": len(body.queries),
        "poll": f"/api/v1/batches/{batch_id}",
    }


@router.get("/{batch_id}")
async def read_batch(
    batch_id: UUID,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> dict[str, Any]:
    """Job status plus every item's answer as it lands."""
    assert user.org_id is not None
    result: dict[str, Any] = await get_batch(
        pool, org_id=user.org_id, user_id=user.user_id, batch_id=batch_id
    )
    return result
