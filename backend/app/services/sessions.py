"""Query sessions: the unit of multi-turn conversation and of history.

A session groups a user's queries so follow-ups can be resolved against what
came before (the contextualization step in :mod:`app.services.ask`). Reads run
under the caller's tenant context, so RLS is the boundary here as everywhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.errors import NotFoundError
from app.repositories.base import tenant_connection
from app.schemas.sessions import SessionDetailOut, SessionOut, SessionTurn

if TYPE_CHECKING:
    import asyncpg

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

# Sessions plus their query count and most recent activity, in one pass.
_SESSION_SELECT = """
    SELECT s.id, s.title, s.created_at,
           count(q.id) AS query_count,
           max(q.created_at) AS last_query_at
    FROM public.query_sessions s
    LEFT JOIN public.queries q ON q.session_id = s.id
"""


def _to_out(row: asyncpg.Record) -> SessionOut:
    return SessionOut(
        id=row["id"],
        title=row["title"],
        query_count=int(row["query_count"] or 0),
        created_at=row["created_at"],
        last_query_at=row["last_query_at"],
    )


class SessionService:
    def __init__(self, pool: DbPool) -> None:
        self._pool = pool

    async def create(self, *, org_id: UUID, user_id: UUID, title: str | None) -> SessionOut:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            row = await conn.fetchrow(
                "INSERT INTO public.query_sessions (org_id, user_id, title) "
                "VALUES ($1, $2, $3) RETURNING id, title, created_at",
                org_id,
                user_id,
                title,
            )
        assert row is not None  # INSERT .. RETURNING
        return SessionOut(
            id=row["id"],
            title=row["title"],
            query_count=0,
            created_at=row["created_at"],
            last_query_at=None,
        )

    async def list_sessions(
        self, *, org_id: UUID, user_id: UUID, limit: int, offset: int, mine: bool
    ) -> tuple[list[SessionOut], int]:
        scope = "AND s.user_id = $2" if mine else ""
        params: list[object] = [org_id]
        if mine:
            params.append(user_id)
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            total = await conn.fetchval(
                f"SELECT count(*) FROM public.query_sessions s WHERE s.org_id = $1 {scope}",
                *params,
            )
            rows = await conn.fetch(
                f"""
                {_SESSION_SELECT}
                WHERE s.org_id = $1 {scope}
                GROUP BY s.id, s.title, s.created_at
                ORDER BY coalesce(max(q.created_at), s.created_at) DESC
                LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
                """,
                *params,
                limit,
                offset,
            )
        return [_to_out(r) for r in rows], int(total or 0)

    async def get_detail(
        self, *, org_id: UUID, user_id: UUID, session_id: UUID
    ) -> SessionDetailOut:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            row = await conn.fetchrow(
                f"""
                {_SESSION_SELECT}
                WHERE s.id = $1
                GROUP BY s.id, s.title, s.created_at
                """,
                session_id,
            )
            if row is None:
                raise NotFoundError("session not found")
            turns = await conn.fetch(
                """
                SELECT q.id AS query_id, q.raw_query, q.contextualized_query, q.status,
                       q.created_at, a.id AS answer_id, a.content, a.confidence, a.abstained
                FROM public.queries q
                LEFT JOIN LATERAL (
                    SELECT id, content, confidence, abstained FROM public.answers
                    WHERE query_id = q.id ORDER BY created_at DESC LIMIT 1
                ) a ON true
                WHERE q.session_id = $1
                ORDER BY q.created_at
                """,
                session_id,
            )
        return SessionDetailOut(
            session=_to_out(row),
            turns=[
                SessionTurn(
                    query_id=t["query_id"],
                    raw_query=t["raw_query"],
                    contextualized_query=t["contextualized_query"],
                    status=t["status"],
                    created_at=t["created_at"],
                    answer_id=t["answer_id"],
                    answer=t["content"],
                    confidence=t["confidence"],
                    abstained=t["abstained"],
                )
                for t in turns
            ],
        )
