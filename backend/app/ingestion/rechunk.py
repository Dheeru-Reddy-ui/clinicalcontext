"""Chunk the shared corpus with a second strategy, alongside the structural
chunks it was ingested with (migration 008 keys chunks by strategy).

The source text is rebuilt from the structural chunks — one section per
distinct ``section`` label, in chunk order — which is the document as the
ingestion parser saw it. Documents that already have chunks under the
strategy are skipped, so the command is idempotent and resumable. The new
chunks are embedded with the configured embedder afterwards.

The ablation (``evals/ablation/full.py``) needs ``fixed`` for its baseline
row; nothing in the product reads any strategy but ``structural``.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import structlog

from app.core.deps import DbPool
from app.ingestion.models import RawDocument, RawSection, SourceType
from app.repositories.base import PgConnection
from app.retrieval.chunking import get_strategy
from app.retrieval.embed import backfill_pending_chunks

logger = structlog.stdlib.get_logger("app.ingestion.rechunk")

_BATCH = 200


async def _pending_documents(conn: PgConnection, strategy: str, limit: int | None) -> list[UUID]:
    rows = await conn.fetch(
        """
        SELECT d.id
        FROM public.documents d
        WHERE d.org_id IS NULL
          AND EXISTS (
            SELECT 1 FROM public.chunks c WHERE c.document_id = d.id AND c.strategy = 'structural'
          )
          AND NOT EXISTS (
            SELECT 1 FROM public.chunks c WHERE c.document_id = d.id AND c.strategy = $1
          )
        ORDER BY d.ingested_at, d.id
        LIMIT $2
        """,
        strategy,
        limit if limit is not None else 1_000_000,
    )
    return [UUID(str(r["id"])) for r in rows]


async def _rebuild(conn: PgConnection, document_id: UUID) -> RawDocument | None:
    header = await conn.fetchrow(
        "SELECT title, abstract, source_type FROM public.documents WHERE id = $1", document_id
    )
    if header is None:
        return None
    rows = await conn.fetch(
        "SELECT section, content FROM public.chunks "
        "WHERE document_id = $1 AND strategy = 'structural' ORDER BY chunk_index",
        document_id,
    )
    sections: list[RawSection] = []
    for row in rows:
        title = row["section"]
        if sections and sections[-1].title == title:
            sections[-1] = RawSection(title, f"{sections[-1].content} {row['content']}")
        else:
            sections.append(RawSection(title, str(row["content"])))
    return RawDocument(
        source_type=cast(SourceType, str(header["source_type"])),
        title=str(header["title"]),
        sections=sections,
        abstract=header["abstract"],
    )


async def rechunk_corpus(
    pool: DbPool, *, strategy: str, embedder_name: str, limit: int | None = None
) -> dict[str, Any]:
    chunker = get_strategy(strategy)
    documents = chunks = 0
    async with pool.acquire() as conn:
        pending = await _pending_documents(conn, strategy, limit)
        logger.info("rechunk_start", strategy=strategy, documents=len(pending))
        for start in range(0, len(pending), _BATCH):
            batch = pending[start : start + _BATCH]
            async with conn.transaction():
                for document_id in batch:
                    document = await _rebuild(conn, document_id)
                    if document is None:
                        continue
                    pieces = await chunker.chunk(document)
                    if not pieces:
                        continue
                    await conn.executemany(
                        """
                        INSERT INTO public.chunks
                            (document_id, chunk_index, content, token_count, section,
                             strategy, embed_text)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        ON CONFLICT (document_id, strategy, chunk_index) DO NOTHING
                        """,
                        [
                            (
                                document_id,
                                piece.chunk_index,
                                piece.content,
                                piece.token_count,
                                piece.section,
                                strategy,
                                piece.embed_text,
                            )
                            for piece in pieces
                        ],
                    )
                    documents += 1
                    chunks += len(pieces)
            logger.info("rechunk_progress", strategy=strategy, documents=documents, chunks=chunks)
    embedded = await backfill_pending_chunks(pool, embedder_name=embedder_name)
    return {
        "strategy": strategy,
        "documents": documents,
        "chunks": chunks,
        "embedded": embedded,
        "embedder": embedder_name,
    }


__all__ = ["rechunk_corpus"]
