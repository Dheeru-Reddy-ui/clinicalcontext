"""Session endpoints — create a conversation, list them, read one's turns."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbPool, get_asyncpg_pool
from app.core.ratelimit import enforce_rate_limit
from app.core.security import CurrentUser
from app.schemas.sessions import (
    SessionCreateRequest,
    SessionDetailOut,
    SessionOut,
    SessionsOut,
)
from app.services.sessions import SessionService

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", status_code=201)
async def create_session(
    body: SessionCreateRequest,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> SessionOut:
    assert user.org_id is not None
    return await SessionService(pool).create(
        org_id=user.org_id, user_id=user.user_id, title=body.title
    )


@router.get("")
async def list_sessions(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    mine: bool = False,
) -> SessionsOut:
    assert user.org_id is not None
    sessions, total = await SessionService(pool).list_sessions(
        org_id=user.org_id, user_id=user.user_id, limit=limit, offset=offset, mine=mine
    )
    return SessionsOut(sessions=sessions, total=total)


@router.get("/{session_id}")
async def get_session(
    session_id: UUID,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> SessionDetailOut:
    """The full thread: every turn, with the query it was resolved to."""
    assert user.org_id is not None
    return await SessionService(pool).get_detail(
        org_id=user.org_id, user_id=user.user_id, session_id=session_id
    )
