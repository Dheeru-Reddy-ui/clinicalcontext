"""Webhook registration and the delivery log."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbPool, get_asyncpg_pool
from app.core.errors import NotFoundError
from app.core.security import CurrentUser, require_role
from app.repositories.tenancy import TenancyRepository
from app.schemas.webhooks import (
    DeliveriesOut,
    WebhookCreatedOut,
    WebhookCreateRequest,
    WebhooksOut,
)
from app.services.webhooks import WebhookService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("", status_code=201)
async def create_webhook(
    body: WebhookCreateRequest,
    user: Annotated[CurrentUser, Depends(require_role("owner"))],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> WebhookCreatedOut:
    """Register an endpoint. The signing secret is returned exactly once."""
    assert user.org_id is not None
    created = await WebhookService(pool).create(
        org_id=user.org_id, user_id=user.user_id, url=body.url, events=list(body.events)
    )
    async with pool.acquire() as conn:
        await TenancyRepository().insert_audit(
            conn,
            org_id=user.org_id,
            user_id=user.user_id,
            action="webhook.created",
            resource_type="webhook",
            resource_id=created.id,
            payload={"url": created.url, "events": created.events},
        )
    return created


@router.get("")
async def list_webhooks(
    user: Annotated[CurrentUser, Depends(require_role("owner"))],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> WebhooksOut:
    assert user.org_id is not None
    return WebhooksOut(
        webhooks=await WebhookService(pool).list_webhooks(org_id=user.org_id, user_id=user.user_id)
    )


@router.get("/deliveries")
async def list_deliveries(
    user: Annotated[CurrentUser, Depends(require_role("owner"))],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DeliveriesOut:
    """Every attempt, with status, retry count and the last error."""
    assert user.org_id is not None
    return await WebhookService(pool).deliveries(
        org_id=user.org_id, user_id=user.user_id, limit=limit, offset=offset
    )


@router.delete("/{webhook_id}", status_code=204)
async def delete_webhook(
    webhook_id: UUID,
    user: Annotated[CurrentUser, Depends(require_role("owner"))],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> None:
    assert user.org_id is not None
    if not await WebhookService(pool).delete(
        org_id=user.org_id, user_id=user.user_id, webhook_id=webhook_id
    ):
        raise NotFoundError("webhook not found")
    async with pool.acquire() as conn:
        await TenancyRepository().insert_audit(
            conn,
            org_id=user.org_id,
            user_id=user.user_id,
            action="webhook.deleted",
            resource_type="webhook",
            resource_id=webhook_id,
        )
