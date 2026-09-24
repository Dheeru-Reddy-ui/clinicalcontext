"""Live literature: search PubMed while the question is asked, keep what it finds.

The stored corpus is a snapshot; medicine is not. When the corpus does not
cover a question — or the question asks what is new — PubMed is searched
there and then, the matching abstracts are filed into the shared corpus
through the ordinary ingestion pipeline (classified, chunked, embedded,
deduplicated), and retrieval runs again. Every question the corpus could not
answer leaves it able to answer the next one, and every answer still cites
passages that exist in the database, so citations, the library and the
evidence timeline work unchanged.

Evidence first: the first search asks only for guidelines, systematic
reviews, meta-analyses and randomised trials; the second, for anything, only
tops up. Europe PMC stands in when PubMed is down. Two limits keep this
inside a free plan: a time budget (a person is waiting) and a ceiling on
the database's size (Supabase's free plan holds 500 MB).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree

import httpx
import structlog

from app.ingestion.models import RawDocument, RawSection
from app.ingestion.pipeline import run_ingest
from app.ingestion.sources.ncbi import NcbiClient
from app.ingestion.sources.pubmed import parse_pubmed_article_set
from app.knowledge.terms import asks_for_recent, search_term

if TYPE_CHECKING:
    import asyncpg
    from redis.asyncio import Redis

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("app.knowledge.live")

EUROPE_PMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# The strongest designs first; PubMed's publication-type tags.
_HIGH_EVIDENCE = (
    "(guideline[pt] OR practice guideline[pt] OR meta-analysis[pt] OR "
    "systematic review[pt] OR randomized controlled trial[pt])"
)
_CACHE_TTL_S = 7 * 24 * 3600
_SIZE_CHECK_TTL_S = 600
# Supabase free: 500 MB. Past this, live search still answers from what it
# finds (nothing is filed) and the nightly job is the only writer.
DEFAULT_MAX_DB_MB = 420


@dataclass(slots=True)
class LiveResult:
    term: str
    searched: bool = False
    found: int = 0
    added: int = 0
    source: str = "pubmed"
    skipped: str | None = None
    pmids: list[str] = field(default_factory=list)
    duration_ms: int = 0
    # What the search returned, in PubMed's order — used for the answer in
    # hand whether or not there was room to file it.
    documents: list[RawDocument] = field(default_factory=list)


def pubmed_query(term: str, *, recent: bool, high_evidence: bool) -> str:
    this_year = date.today().year
    since = this_year - (3 if recent else 15)
    query = (
        f"({term}) AND hasabstract[filter] AND english[lang] "
        f'AND ("{since}"[dp] : "{this_year + 1}"[dp])'
    )
    return f"{query} AND {_HIGH_EVIDENCE}" if high_evidence else query


class LiveLiterature:
    def __init__(
        self,
        pool: DbPool,
        redis: Redis | None = None,
        *,
        ncbi: NcbiClient | None = None,
        http: httpx.AsyncClient | None = None,
        max_db_mb: int = DEFAULT_MAX_DB_MB,
    ) -> None:
        self._pool = pool
        self._redis = redis
        self._ncbi = ncbi
        self._http = http
        self._max_db_mb = max_db_mb

    async def enrich(
        self,
        question: str,
        *,
        limit: int = 6,
        timeout_s: float = 9.0,
        term: str | None = None,
    ) -> LiveResult:
        """Search, file what is new, and say what happened. Never raises:
        a failed search leaves the answer to the stored corpus."""
        started = time.perf_counter()
        term = (term or search_term(question)).strip()
        result = LiveResult(term=term)
        if len(term) < 3:
            result.skipped = "no searchable topic"
            return result
        try:
            await asyncio.wait_for(self._enrich(question, term, limit, result), timeout=timeout_s)
        except TimeoutError:
            result.skipped = result.skipped or "timed out"
            logger.warning("live_search_timeout", term=term, timeout_s=timeout_s)
        except Exception as exc:  # the answer must not depend on PubMed
            result.skipped = f"{type(exc).__name__}"
            logger.warning("live_search_failed", term=term, error=f"{type(exc).__name__}: {exc}")
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "live_search",
            term=term,
            found=result.found,
            added=result.added,
            source=result.source,
            skipped=result.skipped,
            duration_ms=result.duration_ms,
        )
        return result

    async def _enrich(self, question: str, term: str, limit: int, result: LiveResult) -> None:
        recent = asks_for_recent(question)
        documents: list[RawDocument]
        try:
            documents = await self._from_pubmed(term, limit=limit, recent=recent, result=result)
        except Exception as exc:
            logger.warning("pubmed_unavailable", error=f"{type(exc).__name__}: {exc}")
            result.source = "europepmc"
            documents = await self._from_europe_pmc(term, limit=limit, recent=recent)
        result.searched = True
        result.found = len(documents)
        result.pmids = [d.pmid for d in documents if d.pmid]
        result.documents = documents
        if not documents:
            return
        if not await self._room_to_grow():
            result.skipped = "corpus at its size limit"
            return
        result.added = await file_documents(self._pool, documents)

    # -- PubMed -------------------------------------------------------------------------

    async def _pubmed_ids(
        self, client: NcbiClient, term: str, limit: int, recent: bool
    ) -> list[str]:
        cache_key = "live:pubmed:" + hashlib.sha256(f"{term}|{recent}|{limit}".encode()).hexdigest()
        if self._redis is not None:
            cached = await self._redis.get(cache_key)
            if cached:
                ids = json.loads(cached)
                if isinstance(ids, list):
                    return [str(i) for i in ids]
        ids = await client.search_ids(
            db="pubmed",
            term=pubmed_query(term, recent=recent, high_evidence=True),
            limit=limit,
            sort="date" if recent else "relevance",
        )
        if len(ids) < limit:
            extra = await client.search_ids(
                db="pubmed",
                term=pubmed_query(term, recent=recent, high_evidence=False),
                limit=limit,
                sort="date" if recent else "relevance",
            )
            ids += [i for i in extra if i not in ids][: limit - len(ids)]
        if self._redis is not None:
            await self._redis.set(cache_key, json.dumps(ids), ex=_CACHE_TTL_S)
        return ids

    async def _from_pubmed(
        self, term: str, *, limit: int, recent: bool, result: LiveResult
    ) -> list[RawDocument]:
        client = self._ncbi or NcbiClient(timeout=6.0, max_attempts=2)
        try:
            ids = await self._pubmed_ids(client, term, limit, recent)
            if not ids:
                return []
            documents: list[RawDocument] = []
            for batch in await client.fetch_xml_batches(db="pubmed", ids=ids):
                try:
                    documents.extend(parse_pubmed_article_set(batch))
                except ElementTree.ParseError as exc:
                    logger.warning("live_pubmed_unparseable", error=str(exc))
            order = {pmid: i for i, pmid in enumerate(ids)}
            documents.sort(key=lambda d: order.get(d.pmid or "", len(order)))
            return [d for d in documents if d.full_text().strip()]
        finally:
            if self._ncbi is None:
                await client.close()

    # -- Europe PMC (fallback) -------------------------------------------------------------

    async def _from_europe_pmc(self, term: str, *, limit: int, recent: bool) -> list[RawDocument]:
        since = date.today().year - (3 if recent else 15)
        params = {
            "query": f"({term}) AND HAS_ABSTRACT:y AND LANG:eng AND PUB_YEAR:[{since} TO 3000]",
            "format": "json",
            "resultType": "core",
            "pageSize": str(limit),
        }
        if recent:
            params["sort"] = "P_PDATE_D desc"
        client = self._http or httpx.AsyncClient(timeout=6.0)
        try:
            response = await client.get(EUROPE_PMC_SEARCH, params=params)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        finally:
            if self._http is None:
                await client.aclose()
        return parse_europe_pmc(payload)

    # -- growth limit --------------------------------------------------------------------

    async def _room_to_grow(self) -> bool:
        return await room_to_grow(self._pool, self._redis, self._max_db_mb)


async def room_to_grow(
    pool: DbPool, redis: Redis | None, max_db_mb: int = DEFAULT_MAX_DB_MB
) -> bool:
    """Is the database under its ceiling? Checked at most every few minutes
    (the size is cached in Redis), because every filing path asks."""
    cache_key = "live:db_size_mb"
    size_mb: float | None = None
    if redis is not None:
        cached = await redis.get(cache_key)
        if cached:
            size_mb = float(cached)
    if size_mb is None:
        async with pool.acquire() as conn:
            size = await conn.fetchval("SELECT pg_database_size(current_database())")
        size_mb = float(size or 0) / (1024 * 1024)
        if redis is not None:
            await redis.set(cache_key, f"{size_mb:.1f}", ex=_SIZE_CHECK_TTL_S)
    return size_mb < max_db_mb


async def file_documents(pool: DbPool, documents: Sequence[RawDocument]) -> int:
    """Classify, chunk, store and embed ``documents`` in the shared corpus.
    Returns how many were new (a known paper is deduplicated, not re-added)."""
    from app.retrieval.embed import backfill_pending_chunks

    async def _stream() -> AsyncIterator[RawDocument]:
        for document in documents:
            yield document

    stats = await run_ingest(
        pool, _stream(), source="live", query=None, allow_llm=False, embed=False
    )
    if stats.inserted:
        # The local embedder: deterministic, free, and fast enough to run
        # while the person waits (the same one the stored corpus uses).
        await backfill_pending_chunks(pool, embedder_name="local", limit=stats.chunks_written + 50)
    return stats.inserted


def parse_europe_pmc(payload: dict[str, Any]) -> list[RawDocument]:
    """Europe PMC ``resultType=core`` results → RawDocuments."""
    documents: list[RawDocument] = []
    for item in (payload.get("resultList") or {}).get("result", []):
        title = str(item.get("title") or "").strip().rstrip(".")
        abstract = str(item.get("abstractText") or "").strip()
        if not title or not abstract:
            continue
        abstract = _strip_tags(abstract)
        pmid = str(item.get("pmid") or "") or None
        published: date | None = None
        raw_date = str(item.get("firstPublicationDate") or "")
        try:
            published = date.fromisoformat(raw_date) if raw_date else None
        except ValueError:
            year = str(item.get("pubYear") or "")
            published = date(int(year), 1, 1) if year.isdigit() else None
        pub_types = ((item.get("pubTypeList") or {}).get("pubType")) or []
        journal = ((item.get("journalInfo") or {}).get("journal") or {}).get("title")
        doi = str(item.get("doi") or "") or None
        url = (
            f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            if pmid
            else f"https://europepmc.org/article/{item.get('source', 'MED')}/{item.get('id', '')}"
        )
        documents.append(
            RawDocument(
                source_type="pubmed" if pmid else "pmc",
                external_id=pmid or str(item.get("id") or "") or None,
                title=title,
                abstract=abstract,
                sections=[RawSection(title="Abstract", content=abstract)],
                journal=str(journal) if journal else None,
                publication_date=published,
                doi=doi,
                pmid=pmid,
                url=url,
                publication_types=[str(p) for p in pub_types],
            )
        )
    return documents


def _strip_tags(text: str) -> str:
    import re

    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()
