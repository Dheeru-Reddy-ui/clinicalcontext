"""The in-app notification center.

Rows are written by the system (Living Answers, invites); users only read
them and mark them read. Personal by RLS: a user sees exactly their own.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import DbPool, get_asyncpg_pool
from app.core.errors import NotFoundError
from app.core.ratelimit import enforce_rate_limit
from app.core.security import CurrentUser
from app.repositories.base import tenant_connection

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: UUID
    type: str
    payload: dict[str, Any]
    read_at: datetime | None
    created_at: datetime


class NotificationsOut(BaseModel):
    notifications: list[NotificationOut]
    unread: int


def _row(r: Any) -> NotificationOut:
    payload = r["payload"]
    return NotificationOut(
        id=r["id"],
        type=r["type"],
        payload=json.loads(payload) if isinstance(payload, str) else (payload or {}),
        read_at=r["read_at"],
        created_at=r["created_at"],
    )


@router.get("")
async def list_notifications(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    unread_only: bool = False,
) -> NotificationsOut:
    assert user.org_id is not None
    unread_clause = "AND read_at IS NULL" if unread_only else ""
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        rows = await conn.fetch(
            f"SELECT id, type, payload, read_at, created_at FROM public.notifications "
            f"WHERE user_id = $1 {unread_clause} ORDER BY created_at DESC LIMIT $2",
            user.user_id,
            limit,
        )
        unread = await conn.fetchval(
            "SELECT count(*) FROM public.notifications WHERE user_id = $1 AND read_at IS NULL",
            user.user_id,
        )
    return NotificationsOut(notifications=[_row(r) for r in rows], unread=int(unread or 0))


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: UUID,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> NotificationOut:
    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        row = await conn.fetchrow(
            "UPDATE public.notifications SET read_at = coalesce(read_at, now()) "
            "WHERE id = $1 RETURNING id, type, payload, read_at, created_at",
            notification_id,
        )
    if row is None:
        raise NotFoundError("notification not found")
    return _row(row)


@router.post("/read-all", status_code=204)
async def mark_all_read(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> None:
    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        await conn.execute(
            "UPDATE public.notifications SET read_at = now() "
            "WHERE user_id = $1 AND read_at IS NULL",
            user.user_id,
        )
