"""Evidence Binders — collections of answers and passages, annotated in place.

Visibility is decided by RLS (private = creator only, org = org-wide) and the
binder_items/annotations policies ride on it, so every read below simply runs
under the caller's tenant context and sees exactly what it is allowed to.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.errors import NotFoundError
from app.repositories.base import PgConnection, tenant_connection
from app.schemas.binders import (
    AnnotationCreateRequest,
    AnnotationOut,
    AnswerSnapshot,
    BinderCreateRequest,
    BinderDetailOut,
    BinderItemCreateRequest,
    BinderItemOut,
    BinderOut,
    BinderUpdateRequest,
    PassageSnapshot,
)

if TYPE_CHECKING:
    import asyncpg

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool


def _json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    return json.loads(value) if isinstance(value, str) else value


_BINDER_SELECT = """
    SELECT b.id, b.title, b.description, b.visibility::text AS visibility, b.created_by,
           b.created_at, p.full_name AS created_by_name,
           (SELECT count(*) FROM public.binder_items i WHERE i.binder_id = b.id) AS item_count
    FROM public.binders b
    LEFT JOIN public.profiles p ON p.id = b.created_by
"""


def _binder(row: asyncpg.Record) -> BinderOut:
    return BinderOut(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        visibility=row["visibility"],
        created_by=row["created_by"],
        created_by_name=row["created_by_name"],
        item_count=int(row["item_count"] or 0),
        created_at=row["created_at"],
    )


class BinderService:
    def __init__(self, pool: DbPool) -> None:
        self._pool = pool

    # -- binders -------------------------------------------------------------------

    async def create(self, *, org_id: UUID, user_id: UUID, body: BinderCreateRequest) -> BinderOut:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            binder_id = await conn.fetchval(
                "INSERT INTO public.binders (org_id, created_by, title, description, visibility) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING id",
                org_id,
                user_id,
                body.title,
                body.description,
                body.visibility,
            )
            row = await conn.fetchrow(f"{_BINDER_SELECT} WHERE b.id = $1", binder_id)
        assert row is not None
        return _binder(row)

    async def list_binders(self, *, org_id: UUID, user_id: UUID) -> list[BinderOut]:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            rows = await conn.fetch(f"{_BINDER_SELECT} ORDER BY b.created_at DESC")
        return [_binder(r) for r in rows]

    async def update(
        self, *, org_id: UUID, user_id: UUID, binder_id: UUID, body: BinderUpdateRequest
    ) -> BinderOut:
        sets: list[str] = []
        params: list[Any] = []
        for column in ("title", "description", "visibility"):
            value = getattr(body, column)
            if value is not None:
                params.append(value)
                sets.append(f"{column} = ${len(params)}")
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            if sets:
                params.append(binder_id)
                assignments = ", ".join(sets)
                updated = await conn.execute(
                    f"UPDATE public.binders SET {assignments} WHERE id = ${len(params)}",
                    *params,
                )
                if not updated.endswith("1"):
                    raise NotFoundError("binder not found")
            row = await conn.fetchrow(f"{_BINDER_SELECT} WHERE b.id = $1", binder_id)
        if row is None:
            raise NotFoundError("binder not found")
        return _binder(row)

    async def delete(self, *, org_id: UUID, user_id: UUID, binder_id: UUID) -> None:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            status = await conn.execute("DELETE FROM public.binders WHERE id = $1", binder_id)
        if not status.endswith("1"):
            raise NotFoundError("binder not found")

    # -- detail --------------------------------------------------------------------

    async def get_detail(self, *, org_id: UUID, user_id: UUID, binder_id: UUID) -> BinderDetailOut:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            row = await conn.fetchrow(f"{_BINDER_SELECT} WHERE b.id = $1", binder_id)
            if row is None:
                raise NotFoundError("binder not found")
            items = await conn.fetch(
                """
                SELECT i.id, i.item_type::text AS item_type, i.position, i.added_by, i.created_at,
                       i.answer_id, i.chunk_id, p.full_name AS added_by_name
                FROM public.binder_items i
                LEFT JOIN public.profiles p ON p.id = i.added_by
                WHERE i.binder_id = $1
                ORDER BY i.position, i.created_at
                """,
                binder_id,
            )
            answers = await self._answer_snapshots(
                conn, [r["answer_id"] for r in items if r["answer_id"]]
            )
            passages = await self._passage_snapshots(
                conn, [r["chunk_id"] for r in items if r["chunk_id"]]
            )
            annotations = await self._annotations_for_items(conn, [r["id"] for r in items])
        return BinderDetailOut(
            binder=_binder(row),
            items=[
                BinderItemOut(
                    id=r["id"],
                    item_type=r["item_type"],
                    position=r["position"],
                    added_by=r["added_by"],
                    added_by_name=r["added_by_name"],
                    created_at=r["created_at"],
                    answer=answers.get(r["answer_id"]) if r["answer_id"] else None,
                    passage=passages.get(r["chunk_id"]) if r["chunk_id"] else None,
                    annotations=annotations.get(r["id"], []),
                )
                for r in items
            ],
        )

    async def _answer_snapshots(
        self, conn: PgConnection, ids: list[UUID]
    ) -> dict[UUID, AnswerSnapshot]:
        if not ids:
            return {}
        rows = await conn.fetch(
            """
            SELECT a.id, q.raw_query, a.content, a.citations, a.confidence::text AS confidence,
                   a.evidence_grade::text AS evidence_grade, a.has_contradiction, a.abstained,
                   a.reasoning, a.created_at
            FROM public.answers a JOIN public.queries q ON q.id = a.query_id
            WHERE a.id = ANY($1)
            """,
            ids,
        )
        return {
            r["id"]: AnswerSnapshot(
                id=r["id"],
                query=r["raw_query"],
                content=r["content"],
                citations=_json(r["citations"], []),
                confidence=r["confidence"],
                evidence_grade=r["evidence_grade"],
                has_contradiction=r["has_contradiction"],
                abstained=r["abstained"],
                reasoning=_json(r["reasoning"], {}),
                created_at=r["created_at"],
            )
            for r in rows
        }

    async def _passage_snapshots(
        self, conn: PgConnection, ids: list[UUID]
    ) -> dict[UUID, PassageSnapshot]:
        if not ids:
            return {}
        rows = await conn.fetch(
            """
            SELECT c.id AS chunk_id, c.document_id, c.section, c.content,
                   d.title, d.journal, d.publication_date, d.pmid, d.doi,
                   d.evidence_grade::text AS evidence_grade, d.study_type::text AS study_type
            FROM public.chunks c JOIN public.documents d ON d.id = c.document_id
            WHERE c.id = ANY($1)
            """,
            ids,
        )
        return {
            r["chunk_id"]: PassageSnapshot(
                chunk_id=r["chunk_id"],
                document_id=r["document_id"],
                document_title=r["title"],
                section=r["section"],
                content=r["content"],
                journal=r["journal"],
                publication_date=(
                    r["publication_date"].isoformat() if r["publication_date"] else None
                ),
                evidence_grade=r["evidence_grade"],
                study_type=r["study_type"],
                pmid=r["pmid"],
                doi=r["doi"],
            )
            for r in rows
        }

    # -- items ---------------------------------------------------------------------

    async def add_item(
        self, *, org_id: UUID, user_id: UUID, binder_id: UUID, body: BinderItemCreateRequest
    ) -> BinderItemOut:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            visible = await conn.fetchval("SELECT 1 FROM public.binders WHERE id = $1", binder_id)
            if visible is None:
                raise NotFoundError("binder not found")
            # RLS-scoped existence checks turn a foreign id into a 404, not an FK error.
            if body.answer_id is not None:
                ok = await conn.fetchval(
                    "SELECT 1 FROM public.answers WHERE id = $1", body.answer_id
                )
                if ok is None:
                    raise NotFoundError("answer not found")
            if body.chunk_id is not None:
                ok = await conn.fetchval("SELECT 1 FROM public.chunks WHERE id = $1", body.chunk_id)
                if ok is None:
                    raise NotFoundError("passage not found")
            item_id = await conn.fetchval(
                """
                INSERT INTO public.binder_items
                    (binder_id, org_id, item_type, answer_id, chunk_id, position, added_by)
                VALUES ($1, $2, $3, $4, $5,
                        (SELECT coalesce(max(position), 0) + 1
                         FROM public.binder_items WHERE binder_id = $1),
                        $6)
                RETURNING id
                """,
                binder_id,
                org_id,
                body.item_type,
                body.answer_id,
                body.chunk_id,
                user_id,
            )
        detail = await self.get_detail(org_id=org_id, user_id=user_id, binder_id=binder_id)
        for item in detail.items:
            if item.id == item_id:
                return item
        raise NotFoundError("binder item not found")  # pragma: no cover — just inserted

    async def remove_item(
        self, *, org_id: UUID, user_id: UUID, binder_id: UUID, item_id: UUID
    ) -> None:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            status = await conn.execute(
                "DELETE FROM public.binder_items WHERE id = $1 AND binder_id = $2",
                item_id,
                binder_id,
            )
        if not status.endswith("1"):
            raise NotFoundError("binder item not found")

    # -- annotations ---------------------------------------------------------------

    async def add_annotation(
        self,
        *,
        org_id: UUID,
        user_id: UUID,
        binder_id: UUID,
        item_id: UUID,
        body: AnnotationCreateRequest,
    ) -> AnnotationOut:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            item = await conn.fetchrow(
                "SELECT chunk_id FROM public.binder_items WHERE id = $1 AND binder_id = $2",
                item_id,
                binder_id,
            )
            if item is None:
                raise NotFoundError("binder item not found")
            if body.parent_id is not None:
                parent_ok = await conn.fetchval(
                    "SELECT 1 FROM public.annotations WHERE id = $1 AND binder_item_id = $2",
                    body.parent_id,
                    item_id,
                )
                if parent_ok is None:
                    raise NotFoundError("parent annotation not found on this item")
            row = await conn.fetchrow(
                """
                INSERT INTO public.annotations
                    (org_id, user_id, binder_item_id, chunk_id, highlight_range, body, parent_id)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
                RETURNING id, binder_item_id, user_id, body, highlight_range, parent_id, created_at,
                          (SELECT full_name FROM public.profiles WHERE id = $2) AS author_name
                """,
                org_id,
                user_id,
                item_id,
                item["chunk_id"],
                json.dumps(body.highlight_range) if body.highlight_range is not None else None,
                body.body,
                body.parent_id,
            )
        assert row is not None
        return _annotation(row)

    async def _annotations_for_items(
        self, conn: PgConnection, item_ids: list[UUID]
    ) -> dict[UUID, list[AnnotationOut]]:
        """Threaded: top-level annotations per item, replies nested by parent_id."""
        if not item_ids:
            return {}
        rows = await conn.fetch(
            """
            SELECT a.id, a.binder_item_id, a.user_id, a.body, a.highlight_range, a.parent_id,
                   a.created_at, p.full_name AS author_name
            FROM public.annotations a
            LEFT JOIN public.profiles p ON p.id = a.user_id
            WHERE a.binder_item_id = ANY($1)
            ORDER BY a.created_at
            """,
            item_ids,
        )
        nodes: dict[UUID, AnnotationOut] = {r["id"]: _annotation(r) for r in rows}
        roots: dict[UUID, list[AnnotationOut]] = defaultdict(list)
        for row in rows:
            node = nodes[row["id"]]
            parent = nodes.get(row["parent_id"]) if row["parent_id"] else None
            if parent is not None:
                parent.replies.append(node)
            else:
                roots[row["binder_item_id"]].append(node)
        return dict(roots)


def _annotation(row: asyncpg.Record) -> AnnotationOut:
    return AnnotationOut(
        id=row["id"],
        binder_item_id=row["binder_item_id"],
        author_id=row["user_id"],
        author_name=row["author_name"],
        body=row["body"],
        highlight_range=_json(row["highlight_range"], None),
        parent_id=row["parent_id"],
        created_at=row["created_at"],
    )
