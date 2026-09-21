"""Per-tenant API key management (owner only)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.deps import DbPool, get_asyncpg_pool
from app.core.errors import NotFoundError
from app.core.security import CurrentUser, require_role
from app.repositories.tenancy import TenancyRepository
from app.schemas.api_keys import (
    ApiKeyCreatedOut,
    ApiKeyCreateRequest,
    ApiKeysOut,
)
from app.services.api_keys import ApiKeyService

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.post("", status_code=201)
async def create_api_key(
    body: ApiKeyCreateRequest,
    user: Annotated[CurrentUser, Depends(require_role("owner"))],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> ApiKeyCreatedOut:
    assert user.org_id is not None
    created = await ApiKeyService(pool).create(
        org_id=user.org_id, created_by=user.user_id, name=body.name, scopes=body.scopes
    )
    async with pool.acquire() as conn:
        await TenancyRepository().insert_audit(
            conn,
            org_id=user.org_id,
            user_id=user.user_id,
            action="api_key.created",
            resource_type="api_key",
            resource_id=created.id,
            payload={"name": created.name, "scopes": created.scopes, "prefix": created.key_prefix},
        )
    return created


@router.get("")
async def list_api_keys(
    user: Annotated[CurrentUser, Depends(require_role("owner"))],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> ApiKeysOut:
    assert user.org_id is not None
    keys = await ApiKeyService(pool).list_keys(org_id=user.org_id, user_id=user.user_id)
    return ApiKeysOut(api_keys=keys)


@router.delete("/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: UUID,
    user: Annotated[CurrentUser, Depends(require_role("owner"))],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> None:
    assert user.org_id is not None
    revoked = await ApiKeyService(pool).revoke(
        org_id=user.org_id, user_id=user.user_id, key_id=key_id
    )
    if not revoked:
        raise NotFoundError("API key not found or already revoked")
    async with pool.acquire() as conn:
        await TenancyRepository().insert_audit(
            conn,
            org_id=user.org_id,
            user_id=user.user_id,
            action="api_key.revoked",
            resource_type="api_key",
            resource_id=key_id,
        )
