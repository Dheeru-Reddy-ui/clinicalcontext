"""Dense retrieval — pgvector cosine search over the HNSW index.

Tenant-scoped: a caller sees its own org's chunks plus the shared public
corpus (org_id IS NULL), never another tenant's. The query vector travels as
a pgvector text literal (``$1::vector``) so no client-side codec is needed.
Cosine similarity = 1 - cosine distance (the ``<=>`` operator).

``hnsw.ef_search`` matters as much as the index itself: it caps how many
candidates the index considers, *before* the tenant filter removes any, so a
value below the requested limit returns fewer rows than asked for. It is set
per connection in ``app/main.py`` from ``HNSW_EF_SEARCH``.
"""

from __future__ import annotations

from uuid import UUID

from app.repositories.base import PgConnection
from app.retrieval.types import RetrievedChunk


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"


async def dense_search(
    conn: PgConnection,
    *,
    query_vector: list[float],
    org_id: UUID | None,
    strategy: str = "structural",
    limit: int = 50,
) -> list[RetrievedChunk]:
    """Top-``limit`` chunks by cosine similarity to ``query_vector``.

    ``org_id=None`` searches only the shared corpus (the ablation / service
    context); a non-null org additionally includes that org's private chunks.
    """
    # The approximate-nearest-neighbour search is its own subquery, ordered by
    # the distance operator alone. An HNSW index scan can only satisfy a
    # single-column ordering, so adding the deterministic tie-break here (as
    # this query did in Phase 13) silently defeated the index: Postgres fell
    # back to a sequential scan and sort over every vector — 442 ms instead of
    # 4 ms on a 30k-chunk corpus. The tie-break now happens outside, over the
    # candidate set the index returned, which keeps both the index and the
    # determinism.
    rows = await conn.fetch(
        """
        WITH ann AS (
            SELECT e.chunk_id, e.embedding <=> $1::vector AS distance
            FROM public.chunk_embeddings e
            WHERE e.strategy = $2
              AND (e.org_id IS NULL OR e.org_id = $3)
            ORDER BY e.embedding <=> $1::vector
            LIMIT $4
        )
        SELECT
            c.id            AS chunk_id,
            c.document_id   AS document_id,
            c.content       AS content,
            c.section       AS section,
            d.title         AS title,
            d.publication_date AS publication_date,
            d.evidence_grade::text AS evidence_grade,
            d.study_type::text     AS study_type,
            d.journal       AS journal,
            d.pmid          AS pmid,
            d.doi           AS doi,
            d.url           AS url,
            1.0 - ann.distance AS similarity
        FROM ann
        JOIN public.chunks c ON c.id = ann.chunk_id
        JOIN public.documents d ON d.id = c.document_id
        ORDER BY ann.distance, c.id
        """,
        _vector_literal(query_vector),
        strategy,
        org_id,
        limit,
    )
    return [
        RetrievedChunk(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            content=row["content"],
            section=row["section"],
            title=row["title"],
            publication_date=row["publication_date"],
            evidence_grade=row["evidence_grade"],
            study_type=row["study_type"],
            journal=row["journal"],
            pmid=row["pmid"],
            doi=row["doi"],
            url=row["url"],
        ).with_score("dense", float(row["similarity"]))
        for row in rows
    ]
