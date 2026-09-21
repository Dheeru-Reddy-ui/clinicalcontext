"""Query endpoints — streaming ask (SSE), detail, and history."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse

from app.core.deps import DbPool, get_asyncpg_pool, get_redis_client
from app.core.errors import PermissionDeniedError
from app.core.ratelimit import enforce_rate_limit
from app.core.security import CurrentUser
from app.repositories.query_read import QueryReadRepository
from app.schemas.query import QueryRequest
from app.services.ask import AskService

router = APIRouter(prefix="/queries", tags=["queries"])


def _sse(event: dict[str, object]) -> str:
    return f"data: {json.dumps(event)}\n\n"


@router.post("")
async def create_query(
    body: QueryRequest,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    redis: Annotated[Any, Depends(get_redis_client)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> StreamingResponse:
    # Read-only principals (viewer role / read-scoped API keys) cannot query.
    if user.role == "viewer":
        raise PermissionDeniedError("this principal has read-only access and cannot run queries")
    assert user.org_id is not None
    service = AskService(pool, redis)

    async def stream() -> AsyncIterator[str]:
        try:
            async for event in service.ask(
                query=body.query,
                org_id=user.org_id,  # type: ignore[arg-type]
                user_id=user.user_id,
                session_id=body.session_id,
                mode=body.mode,
                entities=body.entities,
                pico=body.pico.model_dump() if body.pico else None,
                idempotency_key=idempotency_key,
                auth_kind=user.auth_kind,
                api_key_prefix=user.api_key_prefix,
            ):
                yield _sse(event)
        except Exception as exc:  # never leak a stack trace into the stream
            yield _sse(
                {
                    "stage": "error",
                    "message": "The request failed.",
                    "data": {"type": type(exc).__name__},
                }
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{query_id}")
async def get_query(
    query_id: UUID,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> dict[str, Any]:
    """Full detail: answer, citations, retrieval traces, guardrail verdict, cost."""
    assert user.org_id is not None
    detail = await QueryReadRepository(pool).get_detail(
        org_id=user.org_id, user_id=user.user_id, query_id=query_id
    )
    return detail


@router.get("")
async def list_queries(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: str | None = None,
    has_contradiction: bool | None = None,
    abstained: bool | None = None,
    mine: bool = False,
) -> dict[str, Any]:
    """Paginated, filterable query history for the org."""
    assert user.org_id is not None
    return await QueryReadRepository(pool).list_history(
        org_id=user.org_id,
        user_id=user.user_id,
        limit=limit,
        offset=offset,
        status=status,
        has_contradiction=has_contradiction,
        abstained=abstained,
        only_user_id=user.user_id if mine else None,
    )
