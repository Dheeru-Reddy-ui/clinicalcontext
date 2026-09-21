"""Living Answers: follow an answer, and read its version history."""

from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.deps import DbPool, get_asyncpg_pool
from app.core.errors import NotFoundError, PermissionDeniedError
from app.core.ratelimit import enforce_rate_limit
from app.core.security import CurrentUser
from app.repositories.base import tenant_connection

router = APIRouter(prefix="/answers", tags=["answers"])


def _json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    return json.loads(value) if isinstance(value, str) else value


@router.post("/{answer_id}/follow", status_code=201)
async def follow_answer(
    answer_id: UUID,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> dict[str, Any]:
    """Watch this answer: you'll be notified when the evidence moves."""
    if user.role == "viewer":
        raise PermissionDeniedError("this principal has read-only access")
    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        if await conn.fetchval("SELECT 1 FROM public.answers WHERE id = $1", answer_id) is None:
            raise NotFoundError("answer not found")
        await conn.execute(
            "INSERT INTO public.followed_answers (answer_id, org_id, user_id) "
            "VALUES ($1, $2, $3) ON CONFLICT (answer_id, user_id) DO NOTHING",
            answer_id,
            user.org_id,
            user.user_id,
        )
    return {"answer_id": str(answer_id), "following": True}


@router.delete("/{answer_id}/follow", status_code=204)
async def unfollow_answer(
    answer_id: UUID,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> None:
    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        status = await conn.execute(
            "DELETE FROM public.followed_answers WHERE answer_id = $1 AND user_id = $2",
            answer_id,
            user.user_id,
        )
    if not status.endswith("1"):
        raise NotFoundError("you are not following this answer")


@router.get("/{answer_id}/versions")
async def answer_versions(
    answer_id: UUID,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> dict[str, Any]:
    """Immutable version history, oldest first. v1 is the answer as delivered."""
    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        if await conn.fetchval("SELECT 1 FROM public.answers WHERE id = $1", answer_id) is None:
            raise NotFoundError("answer not found")
        rows = await conn.fetch(
            "SELECT version, content, citations, confidence, diff, "
            "superseded_by_document_ids, created_at "
            "FROM public.answer_versions WHERE answer_id = $1 ORDER BY version",
            answer_id,
        )
        following = await conn.fetchval(
            "SELECT 1 FROM public.followed_answers WHERE answer_id = $1 AND user_id = $2",
            answer_id,
            user.user_id,
        )
    return {
        "answer_id": str(answer_id),
        "following": following is not None,
        # No rows means the answer has never been superseded — it still stands.
        "superseded": len(rows) > 1,
        "versions": [
            {
                "version": r["version"],
                "content": r["content"],
                "citations": _json(r["citations"], []),
                "confidence": r["confidence"],
                "diff": _json(r["diff"], None),
                "superseded_by_document_ids": [str(d) for d in r["superseded_by_document_ids"]],
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ],
    }
