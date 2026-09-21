"""Read access for browsing the corpus (tenant-scoped via RLS).

Visibility is enforced by RLS, not by this code: under ``tenant_connection``
an authenticated caller sees their own org's documents plus the shared public
corpus (``org_id IS NULL``) and nothing else. The ``scope`` filter here is a
convenience for the UI, never the security boundary.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.errors import NotFoundError
from app.repositories.base import tenant_connection
from app.schemas.documents import (
    ChunkOut,
    DocumentChunksOut,
    DocumentDetail,
    DocumentScope,
    DocumentsOut,
    DocumentSummary,
)

if TYPE_CHECKING:
    import asyncpg

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

_SCOPE_SQL: dict[str, str] = {
    "all": "",
    "shared": "AND d.org_id IS NULL",
    "private": "AND d.org_id IS NOT NULL",
}


def _json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    return json.loads(value) if isinstance(value, str) else value


def _summary(row: asyncpg.Record) -> DocumentSummary:
    return DocumentSummary(
        id=row["id"],
        title=row["title"],
        journal=row["journal"],
        publication_date=row["publication_date"],
        source_type=row["source_type"],
        study_type=row["study_type"],
        evidence_grade=row["evidence_grade"],
        pmid=row["pmid"],
        doi=row["doi"],
        url=row["url"],
        shared=row["org_id"] is None,
        chunk_count=int(row["chunk_count"] or 0),
        ingested_at=row["ingested_at"],
    )


_SUMMARY_COLUMNS = """
    d.id, d.org_id, d.title, d.journal, d.publication_date,
    d.source_type::text AS source_type, d.study_type::text AS study_type,
    d.evidence_grade::text AS evidence_grade, d.pmid, d.doi, d.url, d.ingested_at,
    (SELECT count(*) FROM public.chunks c WHERE c.document_id = d.id) AS chunk_count
"""


class DocumentReadRepository:
    def __init__(self, pool: DbPool) -> None:
        self._pool = pool

    async def list_documents(
        self,
        *,
        org_id: UUID,
        user_id: UUID,
        limit: int,
        offset: int,
        scope: DocumentScope = "all",
        search: str | None = None,
        source_type: str | None = None,
        study_type: str | None = None,
        evidence_grade: str | None = None,
    ) -> DocumentsOut:
        clauses = [_SCOPE_SQL[scope]]
        params: list[Any] = []
        if search:
            params.append(f"%{search}%")
            clauses.append(f"AND d.title ILIKE ${len(params)}")
        if source_type:
            params.append(source_type)
            clauses.append(f"AND d.source_type = ${len(params)}::public.source_type")
        if study_type:
            params.append(study_type)
            clauses.append(f"AND d.study_type = ${len(params)}::public.study_type")
        if evidence_grade:
            params.append(evidence_grade)
            clauses.append(f"AND d.evidence_grade = ${len(params)}::public.evidence_grade")
        where = " ".join(c for c in clauses if c)

        async with tenant_connection(self._pool, org_id, user_id) as conn:
            total = await conn.fetchval(
                f"SELECT count(*) FROM public.documents d WHERE true {where}", *params
            )
            rows = await conn.fetch(
                f"""
                SELECT {_SUMMARY_COLUMNS}
                FROM public.documents d
                WHERE true {where}
                ORDER BY d.ingested_at DESC
                LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
                """,
                *params,
                limit,
                offset,
            )
        return DocumentsOut(
            documents=[_summary(r) for r in rows],
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

    async def get_document(
        self, *, org_id: UUID, user_id: UUID, document_id: UUID
    ) -> DocumentDetail:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {_SUMMARY_COLUMNS}, d.abstract, d.authors, d.metadata, d.classification
                FROM public.documents d WHERE d.id = $1
                """,
                document_id,
            )
        if row is None:
            raise NotFoundError("document not found")
        return DocumentDetail(
            **_summary(row).model_dump(),
            abstract=row["abstract"],
            authors=_json(row["authors"], []),
            metadata=_json(row["metadata"], {}),
            classification=_json(row["classification"], {}),
        )

    async def get_chunks(
        self, *, org_id: UUID, user_id: UUID, document_id: UUID, limit: int, offset: int
    ) -> DocumentChunksOut:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            exists = await conn.fetchval(
                "SELECT 1 FROM public.documents WHERE id = $1", document_id
            )
            if exists is None:
                raise NotFoundError("document not found")
            total = await conn.fetchval(
                "SELECT count(*) FROM public.chunks WHERE document_id = $1", document_id
            )
            rows = await conn.fetch(
                """
                SELECT c.id, c.chunk_index, c.section, c.content, c.token_count, c.strategy,
                       (e.chunk_id IS NOT NULL) AS embedded
                FROM public.chunks c
                LEFT JOIN public.chunk_embeddings e ON e.chunk_id = c.id
                WHERE c.document_id = $1
                ORDER BY c.chunk_index
                LIMIT $2 OFFSET $3
                """,
                document_id,
                limit,
                offset,
            )
        return DocumentChunksOut(
            document_id=document_id,
            chunks=[
                ChunkOut(
                    id=r["id"],
                    chunk_index=r["chunk_index"],
                    section=r["section"],
                    content=r["content"],
                    token_count=r["token_count"],
                    strategy=r["strategy"],
                    embedded=bool(r["embedded"]),
                )
                for r in rows
            ],
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )
