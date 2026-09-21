"""Share-link management and the org-wide public-sharing policy (authenticated).

The unauthenticated slug resolver lives in :mod:`app.api.public`, outside
``/api/v1`` — every v1 route requires a credential, and that invariant is
enforced by a test sweep.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.deps import DbPool, get_asyncpg_pool
from app.core.errors import PermissionDeniedError
from app.core.ratelimit import enforce_rate_limit
from app.core.security import CurrentUser, require_role
from app.repositories.tenancy import TenancyRepository
from app.schemas.sharing import (
    ShareLinkCreateRequest,
    ShareLinkOut,
    ShareLinksOut,
    SharingPolicyOut,
    SharingPolicyUpdate,
)
from app.services.sharing import SharingService

router = APIRouter(prefix="/sharing", tags=["sharing"])


@router.post("/links", status_code=201)
async def create_share_link(
    body: ShareLinkCreateRequest,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> ShareLinkOut:
    if user.role == "viewer":
        raise PermissionDeniedError("this principal has read-only access and cannot share")
    assert user.org_id is not None
    link = await SharingService(pool).create_link(
        org_id=user.org_id, user_id=user.user_id, answer_id=body.answer_id
    )
    async with pool.acquire() as conn:
        await TenancyRepository().insert_audit(
            conn,
            org_id=user.org_id,
            user_id=user.user_id,
            action="answer.shared",
            resource_type="answer",
            resource_id=body.answer_id,
            payload={"slug": link.slug},
        )
    return link


@router.get("/links")
async def list_share_links(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> ShareLinksOut:
    assert user.org_id is not None
    links = await SharingService(pool).list_links(org_id=user.org_id, user_id=user.user_id)
    return ShareLinksOut(links=links)


@router.delete("/links/{link_id}", status_code=204)
async def revoke_share_link(
    link_id: UUID,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> None:
    if user.role == "viewer":
        raise PermissionDeniedError("this principal has read-only access")
    assert user.org_id is not None
    await SharingService(pool).revoke_link(
        org_id=user.org_id, user_id=user.user_id, link_id=link_id
    )
    async with pool.acquire() as conn:
        await TenancyRepository().insert_audit(
            conn,
            org_id=user.org_id,
            user_id=user.user_id,
            action="answer.share_revoked",
            resource_type="share_link",
            resource_id=link_id,
        )


@router.get("/policy")
async def get_sharing_policy(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> SharingPolicyOut:
    assert user.org_id is not None
    enabled = await SharingService(pool).get_policy(org_id=user.org_id, user_id=user.user_id)
    return SharingPolicyOut(public_sharing_enabled=enabled)


@router.patch("/policy")
async def set_sharing_policy(
    body: SharingPolicyUpdate,
    user: Annotated[CurrentUser, Depends(require_role("owner"))],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> SharingPolicyOut:
    """Owner-only tenant kill switch: off makes every public link a 404."""
    assert user.org_id is not None
    enabled = await SharingService(pool).set_policy(
        org_id=user.org_id, enabled=body.public_sharing_enabled
    )
    async with pool.acquire() as conn:
        await TenancyRepository().insert_audit(
            conn,
            org_id=user.org_id,
            user_id=user.user_id,
            action="org.sharing_policy_changed",
            resource_type="organization",
            resource_id=user.org_id,
            payload={"public_sharing_enabled": enabled},
        )
    return SharingPolicyOut(public_sharing_enabled=enabled)
