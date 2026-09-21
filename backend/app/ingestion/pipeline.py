"""The ingestion pipeline: fetch → dedupe → parse → classify → chunk → embed → upsert.

Idempotent by construction: identity is the content hash, enforced by the
documents.content_hash unique constraint — re-running any ingest inserts
zero duplicate rows. Failures never abort the run: each failing document
lands in ingestion_failures with enough payload to replay, and the pipeline
moves on.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING

import structlog

from app.ingestion.classifier import classify_document
from app.ingestion.models import IngestStats, RawDocument
from app.ingestion.sources.guideline import parse_guideline_pdf
from app.ingestion.sources.ncbi import NcbiClient
from app.ingestion.sources.pmc import fetch_pmc
from app.ingestion.sources.pubmed import fetch_pubmed
from app.repositories.corpus import CorpusRepository
from app.retrieval.chunking import chunk_sections
from app.retrieval.embed import backfill_pending_chunks

if TYPE_CHECKING:
    import asyncpg

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("app.ingestion.pipeline")

ProgressCallback = Callable[[IngestStats], Awaitable[None]]

_WHITESPACE = re.compile(r"\s+")
_PROGRESS_EVERY = 25


def content_hash_for(document: RawDocument) -> str:
    """Stable identity: normalized title + normalized body text."""
    normalized_title = _WHITESPACE.sub(" ", document.title).strip().lower()
    normalized_text = _WHITESPACE.sub(" ", document.full_text()).strip().lower()
    digest = hashlib.sha256()
    digest.update(normalized_title.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(normalized_text.encode("utf-8"))
    return digest.hexdigest()


async def run_ingest(
    pool: DbPool,
    documents: AsyncIterator[RawDocument],
    *,
    source: str,
    query: str | None = None,
    allow_llm: bool = True,
    embed: bool = True,
    progress_cb: ProgressCallback | None = None,
) -> IngestStats:
    """Drive a stream of RawDocuments through classify → chunk → persist,
    then run the embedding backfill stage."""
    stats = IngestStats(source=source, query=query)
    repo = CorpusRepository()

    async with pool.acquire() as conn:
        async for document in documents:
            stats.fetched += 1
            try:
                if not document.full_text().strip():
                    stats.skipped_no_text += 1
                    continue
                classification = await classify_document(document, allow_llm=allow_llm)
                chunks = chunk_sections(document.sections)
                if not chunks:
                    stats.skipped_no_text += 1
                    continue
                document_id = await repo.insert_document_with_chunks(
                    conn,
                    document=document,
                    content_hash=content_hash_for(document),
                    classification=classification,
                    chunks=chunks,
                )
                if document_id is None:
                    stats.deduplicated += 1
                else:
                    stats.inserted += 1
                    stats.chunks_written += len(chunks)
            except Exception as exc:
                stats.failed += 1
                logger.error(
                    "document_ingest_failed",
                    source=source,
                    external_id=document.external_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
                try:
                    await repo.record_failure(
                        conn,
                        source=source,
                        external_id=document.external_id,
                        stage="persist",
                        error=f"{type(exc).__name__}: {exc}",
                        payload={"title": document.title, "pmid": document.pmid},
                    )
                except Exception:  # dead-lettering must never kill the run
                    logger.exception("dead_letter_write_failed")
            if progress_cb is not None and stats.fetched % _PROGRESS_EVERY == 0:
                await progress_cb(stats)

    if embed:
        try:
            stats.embedded = await backfill_pending_chunks(pool)
        except Exception as exc:
            # Embedding is a resumable, deferrable stage — a missing/invalid
            # provider key must not fail the whole ingest.
            logger.warning(
                "embedding_deferred",
                reason=f"{type(exc).__name__}: {exc}",
                hint="run `python -m app.ingestion.cli embed-pending` once COHERE_API_KEY is real",
            )
    async with pool.acquire() as conn:
        stats.embedding_pending = await repo.count_chunks_pending_embedding(conn)

    if progress_cb is not None:
        await progress_cb(stats)
    logger.info("ingest_complete", **stats.as_dict())
    return stats


async def ingest_from_source(
    pool: DbPool,
    *,
    source: str,
    query: str | None = None,
    limit: int = 100,
    file_path: str | None = None,
    title: str | None = None,
    allow_llm: bool = True,
    embed: bool = True,
    progress_cb: ProgressCallback | None = None,
) -> IngestStats:
    """Resolve a source name to its document stream and run the pipeline."""
    if source in ("pubmed", "pmc"):
        if not query:
            raise ValueError(f"--query is required for source {source!r}")
        client = NcbiClient()
        try:
            fetcher = fetch_pubmed if source == "pubmed" else fetch_pmc
            return await run_ingest(
                pool,
                fetcher(client, query=query, limit=limit),
                source=source,
                query=query,
                allow_llm=allow_llm,
                embed=embed,
                progress_cb=progress_cb,
            )
        finally:
            await client.close()

    if source == "guideline":
        if not file_path:
            raise ValueError("--file is required for source 'guideline'")
        from pathlib import Path

        from app.ingestion.storage import upload_guideline_pdf

        pdf_path = Path(file_path)
        document = parse_guideline_pdf(pdf_path, title=title)
        document.storage_path = await upload_guideline_pdf(pdf_path)

        async def _single() -> AsyncIterator[RawDocument]:
            yield document

        return await run_ingest(
            pool,
            _single(),
            source=source,
            query=None,
            allow_llm=allow_llm,
            embed=embed,
            progress_cb=progress_cb,
        )

    raise ValueError(f"unknown source: {source!r}")
