"""Conversations: sessions of kind chat/learn, and the turns inside them.

A conversation is the same record Ask keeps — a session, its queries and
their answers — so it sits behind the same row-level security and audit
trail; these are the reads and writes the chat surface needs on top.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.repositories.base import PgConnection


class ChatRepository:
    async def create_session(
        self,
        conn: PgConnection,
        *,
        org_id: UUID,
        user_id: UUID,
        title: str,
        kind: str,
    ) -> UUID:
        row = await conn.fetchval(
            "INSERT INTO public.query_sessions (org_id, user_id, title, kind) "
            "VALUES ($1, $2, $3, $4) RETURNING id",
            org_id,
            user_id,
            title,
            kind,
        )
        return UUID(str(row))

    async def owns_session(self, conn: PgConnection, *, session_id: UUID, user_id: UUID) -> bool:
        return bool(
            await conn.fetchval(
                "SELECT 1 FROM public.query_sessions WHERE id = $1 AND user_id = $2",
                session_id,
                user_id,
            )
        )

    async def create_query(
        self,
        conn: PgConnection,
        *,
        session_id: UUID,
        org_id: UUID,
        user_id: UUID,
        raw_query: str,
        audience: str,
        contextualized_query: str | None,
    ) -> UUID:
        row = await conn.fetchval(
            "INSERT INTO public.queries "
            "(session_id, org_id, user_id, raw_query, status, mode, audience, "
            " contextualized_query) "
            "VALUES ($1, $2, $3, $4, 'running', 'standard', $5, $6) RETURNING id",
            session_id,
            org_id,
            user_id,
            raw_query,
            audience,
            contextualized_query,
        )
        await conn.execute(
            "UPDATE public.query_sessions SET updated_at = now() WHERE id = $1", session_id
        )
        return UUID(str(row))

    async def history(
        self, conn: PgConnection, *, session_id: UUID, limit: int = 6
    ) -> list[tuple[str, str]]:
        """The last ``limit`` exchanges, oldest first: (question, answer)."""
        rows = await conn.fetch(
            """
            SELECT q.raw_query, coalesce(a.content, '') AS answer
            FROM public.queries q
            LEFT JOIN LATERAL (
                SELECT content FROM public.answers
                WHERE query_id = q.id ORDER BY created_at DESC LIMIT 1
            ) a ON true
            WHERE q.session_id = $1 AND q.status <> 'blocked'
            ORDER BY q.created_at DESC
            LIMIT $2
            """,
            session_id,
            limit,
        )
        return [(str(r["raw_query"]), str(r["answer"])) for r in reversed(rows)]

    async def list_sessions(
        self, conn: PgConnection, *, user_id: UUID, kinds: list[str], limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = await conn.fetch(
            """
            SELECT s.id, s.title, s.kind, s.created_at, s.updated_at,
                   (SELECT count(*) FROM public.queries q WHERE q.session_id = s.id) AS turns
            FROM public.query_sessions s
            WHERE s.user_id = $1 AND s.kind = ANY($2::text[])
            ORDER BY s.updated_at DESC
            LIMIT $3
            """,
            user_id,
            kinds,
            limit,
        )
        return [dict(r) for r in rows]

    async def session_messages(
        self, conn: PgConnection, *, session_id: UUID, user_id: UUID
    ) -> dict[str, Any] | None:
        session = await conn.fetchrow(
            "SELECT id, title, kind, created_at, updated_at FROM public.query_sessions "
            "WHERE id = $1 AND user_id = $2",
            session_id,
            user_id,
        )
        if session is None:
            return None
        rows = await conn.fetch(
            """
            SELECT q.id AS query_id, q.raw_query, q.audience, q.status, q.created_at,
                   a.id AS answer_id, a.content, a.citations, a.model, a.reasoning
            FROM public.queries q
            LEFT JOIN LATERAL (
                SELECT id, content, citations, model, reasoning FROM public.answers
                WHERE query_id = q.id ORDER BY created_at DESC LIMIT 1
            ) a ON true
            WHERE q.session_id = $1
            ORDER BY q.created_at
            """,
            session_id,
        )
        turns: list[dict[str, Any]] = []
        for r in rows:
            citations = r["citations"]
            reasoning = r["reasoning"]
            turns.append(
                {
                    "query_id": r["query_id"],
                    "question": r["raw_query"],
                    "audience": r["audience"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "answer_id": r["answer_id"],
                    "answer": r["content"],
                    "citations": json.loads(citations)
                    if isinstance(citations, str)
                    else (citations or []),
                    "model": r["model"],
                    "details": (json.loads(reasoning) if isinstance(reasoning, str) else reasoning)
                    or {},
                }
            )
        return {**dict(session), "turns": turns}

    async def rename_session(
        self, conn: PgConnection, *, session_id: UUID, user_id: UUID, title: str
    ) -> bool:
        result = await conn.execute(
            "UPDATE public.query_sessions SET title = $3 WHERE id = $1 AND user_id = $2",
            session_id,
            user_id,
            title,
        )
        return str(result).endswith(" 1")
