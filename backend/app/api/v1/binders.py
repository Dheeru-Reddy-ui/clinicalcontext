"""Evidence Binder endpoints: collections, items, and threaded annotations."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.deps import DbPool, get_asyncpg_pool
from app.core.errors import PermissionDeniedError
from app.core.ratelimit import enforce_rate_limit
from app.core.security import CurrentUser
from app.schemas.binders import (
    AnnotationCreateRequest,
    AnnotationOut,
    BinderCreateRequest,
    BinderDetailOut,
    BinderItemCreateRequest,
    BinderItemOut,
    BinderOut,
    BindersOut,
    BinderUpdateRequest,
)
from app.services.binders import BinderService

router = APIRouter(prefix="/binders", tags=["binders"])


def _writer(user: CurrentUser) -> CurrentUser:
    # Viewers (and read-scoped API keys) may read org binders but never curate.
    if user.role == "viewer":
        raise PermissionDeniedError("this principal has read-only access")
    return user


@router.post("", status_code=201)
async def create_binder(
    body: BinderCreateRequest,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> BinderOut:
    _writer(user)
    assert user.org_id is not None
    return await BinderService(pool).create(org_id=user.org_id, user_id=user.user_id, body=body)


@router.get("")
async def list_binders(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> BindersOut:
    assert user.org_id is not None
    binders = await BinderService(pool).list_binders(org_id=user.org_id, user_id=user.user_id)
    return BindersOut(binders=binders)


@router.get("/{binder_id}")
async def get_binder(
    binder_id: UUID,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> BinderDetailOut:
    """The reading view: every item resolved, with its annotation threads."""
    assert user.org_id is not None
    return await BinderService(pool).get_detail(
        org_id=user.org_id, user_id=user.user_id, binder_id=binder_id
    )


@router.patch("/{binder_id}")
async def update_binder(
    binder_id: UUID,
    body: BinderUpdateRequest,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> BinderOut:
    _writer(user)
    assert user.org_id is not None
    return await BinderService(pool).update(
        org_id=user.org_id, user_id=user.user_id, binder_id=binder_id, body=body
    )


@router.delete("/{binder_id}", status_code=204)
async def delete_binder(
    binder_id: UUID,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> None:
    _writer(user)
    assert user.org_id is not None
    await BinderService(pool).delete(org_id=user.org_id, user_id=user.user_id, binder_id=binder_id)


@router.post("/{binder_id}/items", status_code=201)
async def add_binder_item(
    binder_id: UUID,
    body: BinderItemCreateRequest,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> BinderItemOut:
    _writer(user)
    assert user.org_id is not None
    return await BinderService(pool).add_item(
        org_id=user.org_id, user_id=user.user_id, binder_id=binder_id, body=body
    )


@router.delete("/{binder_id}/items/{item_id}", status_code=204)
async def remove_binder_item(
    binder_id: UUID,
    item_id: UUID,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> None:
    _writer(user)
    assert user.org_id is not None
    await BinderService(pool).remove_item(
        org_id=user.org_id, user_id=user.user_id, binder_id=binder_id, item_id=item_id
    )


@router.post("/{binder_id}/items/{item_id}/annotations", status_code=201)
async def add_annotation(
    binder_id: UUID,
    item_id: UUID,
    body: AnnotationCreateRequest,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> AnnotationOut:
    """Highlight-and-annotate a passage, or reply in a thread (parent_id)."""
    _writer(user)
    assert user.org_id is not None
    return await BinderService(pool).add_annotation(
        org_id=user.org_id,
        user_id=user.user_id,
        binder_id=binder_id,
        item_id=item_id,
        body=body,
    )
