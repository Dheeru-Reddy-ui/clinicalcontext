"""What is new in a specialty: PubMed's most recent high-evidence papers.

For the Learn tab. Guidelines, meta-analyses, systematic reviews and
randomised trials from the last months, newest first, with each paper's
journal, date and design — fetched from PubMed itself, so "latest" means
this week's indexing, not the corpus snapshot. Cached for a few hours per
specialty (PubMed does not change faster than that, and the free tier's
rate limit is shared).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from app.ingestion.sources.ncbi import NcbiClient
from app.knowledge.specialties import Specialty

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = structlog.stdlib.get_logger("app.knowledge.feed")

_CACHE_TTL_S = 6 * 3600
_DESIGNS = (
    "(guideline[pt] OR practice guideline[pt] OR meta-analysis[pt] OR "
    "systematic review[pt] OR randomized controlled trial[pt])"
)
_DESIGN_LABELS = (
    ("Practice Guideline", "Guideline"),
    ("Guideline", "Guideline"),
    ("Meta-Analysis", "Meta-analysis"),
    ("Systematic Review", "Systematic review"),
    ("Randomized Controlled Trial", "Randomised trial"),
    ("Review", "Review"),
)


@dataclass(frozen=True, slots=True)
class FeedItem:
    pmid: str
    title: str
    journal: str
    published: str
    design: str
    url: str


def feed_query(specialty: Specialty, *, days: int) -> str:
    """The specialty's subjects as *major* topics (a paper about the field,
    not one that mentions it), English, with an abstract, recent."""
    since = (date.today() - timedelta(days=days)).strftime("%Y/%m/%d")
    subject = specialty.pubmed.replace("[MeSH Terms]", "[majr]")
    return (
        f"({subject}) AND {_DESIGNS} AND english[lang] AND hasabstract "
        f'AND ("{since}"[dp] : "3000"[dp])'
    )


def _design(pub_types: list[str]) -> str:
    for needle, label in _DESIGN_LABELS:
        if needle in pub_types:
            return label
    return "Study"


def items_from_summaries(summaries: list[dict[str, Any]]) -> list[FeedItem]:
    items: list[FeedItem] = []
    for summary in summaries:
        pmid = str(summary.get("uid") or "")
        title = str(summary.get("title") or "").strip().rstrip(".")
        if not pmid or not title:
            continue
        items.append(
            FeedItem(
                pmid=pmid,
                title=title,
                journal=str(summary.get("fulljournalname") or summary.get("source") or ""),
                published=str(summary.get("sortpubdate") or summary.get("pubdate") or "")[:10],
                design=_design([str(t) for t in summary.get("pubtype") or []]),
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            )
        )
    return items


async def latest_research(
    specialty: Specialty,
    *,
    redis: Redis | None = None,
    days: int = 180,
    limit: int = 12,
    client: NcbiClient | None = None,
) -> list[FeedItem]:
    """Newest high-evidence papers for ``specialty``. Raises on a PubMed
    failure with nothing cached — the caller says so rather than showing an
    empty list as if nothing had been published."""
    cache_key = f"feed:{specialty.slug}:{days}:{limit}"
    if redis is not None:
        cached = await redis.get(cache_key)
        if cached:
            return [FeedItem(**item) for item in json.loads(cached)]
    ncbi = client or NcbiClient(timeout=8.0, max_attempts=2)
    try:
        ids = await ncbi.search_ids(
            # Most recently added first: "latest" as PubMed indexes it, not
            # the issue dates printed months ahead.
            db="pubmed",
            term=feed_query(specialty, days=days),
            limit=limit,
            sort=None,
        )
        items = items_from_summaries(await ncbi.summaries(db="pubmed", ids=ids))
    finally:
        if client is None:
            await ncbi.close()
    if redis is not None:
        await redis.set(cache_key, json.dumps([asdict(i) for i in items]), ex=_CACHE_TTL_S)
    logger.info("research_feed", specialty=specialty.slug, items=len(items))
    return items
