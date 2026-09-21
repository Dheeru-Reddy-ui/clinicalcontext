"""Corpus freshness: is what we hold still current with the source?

"Our newest paper is from March" means nothing on its own. So the weekly job
re-runs each seed domain's query against PubMed, takes the PMIDs the source
returns, and counts how many of them we do **not** have. That missing count is
the honest staleness signal; everything else on the row is context for it.

The job needs network access (NCBI). The read path does not — it only reads
what the last run recorded, including when that was, so a stale table reports
itself as stale rather than silently looking fresh.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import structlog

from app.ingestion.seed import SEED_PLAN

if TYPE_CHECKING:
    import asyncpg

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("app.services.freshness")

# A domain counts as stale once the source has this many papers we lack.
STALE_THRESHOLD = 5


@dataclass(slots=True)
class DomainFreshness:
    domain: str
    last_query: str | None
    document_count: int
    source_checked_count: int
    source_new_count: int
    newest_publication: date | None
    source_newest_publication: date | None
    last_ingested_at: datetime | None
    checked_at: datetime | None

    @property
    def stale(self) -> bool:
        return self.source_new_count >= STALE_THRESHOLD

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "last_query": self.last_query,
            "document_count": self.document_count,
            "source_checked_count": self.source_checked_count,
            "source_new_count": self.source_new_count,
            "newest_publication": (
                self.newest_publication.isoformat() if self.newest_publication else None
            ),
            "source_newest_publication": (
                self.source_newest_publication.isoformat()
                if self.source_newest_publication
                else None
            ),
            "last_ingested_at": (
                self.last_ingested_at.isoformat() if self.last_ingested_at else None
            ),
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "stale": self.stale,
        }


def _row_to_domain(row: asyncpg.Record) -> DomainFreshness:
    return DomainFreshness(
        domain=row["domain"],
        last_query=row["last_query"],
        document_count=int(row["document_count"] or 0),
        source_checked_count=int(row["source_checked_count"] or 0),
        source_new_count=int(row["source_new_count"] or 0),
        newest_publication=row["newest_publication"],
        source_newest_publication=row["source_newest_publication"],
        last_ingested_at=row["last_ingested_at"],
        checked_at=row["checked_at"],
    )


async def read_freshness(pool: DbPool) -> dict[str, Any]:
    """What the last freshness run recorded (no network)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM public.corpus_freshness ORDER BY source_new_count DESC, domain"
        )
        corpus = await conn.fetchrow(
            """
            SELECT count(*) AS documents,
                   max(publication_date) AS newest_publication,
                   max(ingested_at) AS last_ingested_at
            FROM public.documents WHERE org_id IS NULL
            """
        )
    domains = [_row_to_domain(r) for r in rows]
    checked = [d.checked_at for d in domains if d.checked_at]
    assert corpus is not None
    return {
        "corpus": {
            "documents": int(corpus["documents"] or 0),
            "newest_publication": (
                corpus["newest_publication"].isoformat() if corpus["newest_publication"] else None
            ),
            "last_ingested_at": (
                corpus["last_ingested_at"].isoformat() if corpus["last_ingested_at"] else None
            ),
        },
        # None (not a date) when the job has never run — the read path must not
        # imply freshness it cannot vouch for.
        "last_checked_at": min(checked).isoformat() if checked else None,
        "stale_domains": [d.domain for d in domains if d.stale],
        "domains": [d.as_dict() for d in domains],
    }


async def refresh_corpus_freshness(pool: DbPool, *, per_query: int = 50) -> list[DomainFreshness]:
    """Re-query every seed domain against PubMed and record the gap.

    Requires network access to NCBI. Each domain's seed queries are run, the
    returned PMIDs are compared against what we already store, and the delta is
    written to corpus_freshness.
    """
    from app.ingestion.sources.ncbi import NcbiClient

    client = NcbiClient()
    by_domain: dict[str, list[str]] = {}
    for item in SEED_PLAN:
        by_domain.setdefault(item.domain, []).append(item.query)

    results: list[DomainFreshness] = []
    try:
        async with pool.acquire() as conn:
            for domain, queries in by_domain.items():
                pmids: set[str] = set()
                last_query: str | None = None
                for query in queries:
                    last_query = query
                    found = await client.search_ids(db="pubmed", term=query, limit=per_query)
                    pmids.update(str(p) for p in found)
                if not pmids:
                    logger.warning("freshness_no_results", domain=domain)
                    continue

                row = await conn.fetchrow(
                    """
                    SELECT count(*) AS have,
                           max(publication_date) AS newest,
                           max(ingested_at) AS last_ingested
                    FROM public.documents
                    WHERE pmid = ANY($1::text[])
                    """,
                    sorted(pmids),
                )
                assert row is not None
                have = int(row["have"] or 0)
                freshness = DomainFreshness(
                    domain=domain,
                    last_query=last_query,
                    document_count=have,
                    source_checked_count=len(pmids),
                    source_new_count=max(len(pmids) - have, 0),
                    newest_publication=row["newest"],
                    source_newest_publication=None,
                    last_ingested_at=row["last_ingested"],
                    checked_at=datetime.now(tz=UTC),
                )
                await conn.execute(
                    """
                    INSERT INTO public.corpus_freshness (
                        domain, last_query, last_ingested_at, newest_publication,
                        document_count, checked_at, source_new_count, source_checked_count
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (domain) DO UPDATE SET
                        last_query = excluded.last_query,
                        last_ingested_at = excluded.last_ingested_at,
                        newest_publication = excluded.newest_publication,
                        document_count = excluded.document_count,
                        checked_at = excluded.checked_at,
                        source_new_count = excluded.source_new_count,
                        source_checked_count = excluded.source_checked_count
                    """,
                    freshness.domain,
                    freshness.last_query,
                    freshness.last_ingested_at,
                    freshness.newest_publication,
                    freshness.document_count,
                    freshness.checked_at,
                    freshness.source_new_count,
                    freshness.source_checked_count,
                )
                results.append(freshness)
                logger.info(
                    "freshness_checked",
                    domain=domain,
                    have=have,
                    source=len(pmids),
                    missing=freshness.source_new_count,
                )
    finally:
        await client.close()
    return results
