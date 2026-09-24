"""Nightly: each specialty's newest strong evidence, filed into the corpus.

Live search files what a question needed; this files what was published —
for every MBBS subject and PG specialty, the newest guidelines,
meta-analyses, systematic reviews and randomised trials PubMed added in the
last weeks, a few per specialty per night. The corpus then carries this
month's evidence before anyone asks, and the size ceiling keeps it inside
the free database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from xml.etree import ElementTree

import structlog

from app.ingestion.sources.ncbi import NcbiClient
from app.ingestion.sources.pubmed import parse_pubmed_article_set
from app.knowledge.feed import feed_query
from app.knowledge.live import DEFAULT_MAX_DB_MB, file_documents
from app.knowledge.specialties import SPECIALTIES, Specialty

if TYPE_CHECKING:
    import asyncpg

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("app.knowledge.refresh")


async def database_mb(pool: DbPool) -> float:
    async with pool.acquire() as conn:
        size = await conn.fetchval("SELECT pg_database_size(current_database())")
    return float(size or 0) / (1024 * 1024)


async def refresh_latest(
    pool: DbPool,
    *,
    per_specialty: int = 3,
    days: int = 30,
    specialties: tuple[Specialty, ...] = SPECIALTIES,
    client: NcbiClient | None = None,
    max_db_mb: int = DEFAULT_MAX_DB_MB,
) -> dict[str, int]:
    summary = {"specialties": 0, "found": 0, "added": 0, "failed": 0, "stopped_at_limit": 0}
    ncbi = client or NcbiClient(timeout=20.0, max_attempts=3)
    try:
        for specialty in specialties:
            if await database_mb(pool) >= max_db_mb:
                summary["stopped_at_limit"] = 1
                logger.warning("refresh_stopped_at_size_limit", max_db_mb=max_db_mb)
                break
            try:
                ids = await ncbi.search_ids(
                    db="pubmed",
                    term=feed_query(specialty, days=days),
                    limit=per_specialty,
                    sort=None,
                )
                documents = []
                for batch in await ncbi.fetch_xml_batches(db="pubmed", ids=ids) if ids else []:
                    try:
                        documents.extend(parse_pubmed_article_set(batch))
                    except ElementTree.ParseError:
                        continue
                documents = [d for d in documents if d.full_text().strip()]
                summary["found"] += len(documents)
                if documents:
                    summary["added"] += await file_documents(pool, documents)
                summary["specialties"] += 1
            except Exception as exc:  # one specialty's failure is not the night's
                summary["failed"] += 1
                logger.warning(
                    "refresh_specialty_failed",
                    specialty=specialty.slug,
                    error=f"{type(exc).__name__}: {exc}",
                )
    finally:
        if client is None:
            await ncbi.close()
    logger.info("latest_research_refreshed", **summary)
    return summary
