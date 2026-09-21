"""Reproducible seed corpus: a fixed query plan across four clinical domains.

The domain spread (cardiology, infectious disease, endocrinology, psychiatry)
is deliberate — cross-domain and within-domain guideline disagreement is what
the contradiction-detection work in later phases needs to chew on.

Run:
    uv run python -m app.ingestion.seed --per-query 600
    uv run python -m app.ingestion.seed --dry-run          # print the plan only

Idempotent: re-running inserts only documents not already present (content
hash). One shared NcbiClient is used across every query so the NCBI rate
limit is honored globally, not per query.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import asyncpg
import structlog

from app.config import get_settings
from app.ingestion.models import IngestStats
from app.ingestion.pipeline import run_ingest
from app.ingestion.sources.ncbi import NcbiClient
from app.ingestion.sources.pubmed import fetch_pubmed
from app.repositories.corpus import CorpusRepository

if TYPE_CHECKING:
    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("app.ingestion.seed")


@dataclass(frozen=True, slots=True)
class SeedQuery:
    domain: str
    query: str


# hasabstract[Filter] keeps the yield of usable (text-bearing) records high.
SEED_PLAN: list[SeedQuery] = [
    # Cardiology
    SeedQuery("cardiology", "atrial fibrillation anticoagulation"),
    SeedQuery("cardiology", "heart failure reduced ejection fraction treatment"),
    SeedQuery("cardiology", "statin primary prevention cardiovascular"),
    SeedQuery("cardiology", "hypertension management adults"),
    SeedQuery("cardiology", "acute coronary syndrome antiplatelet"),
    # Infectious disease
    SeedQuery("infectious_disease", "community acquired pneumonia treatment"),
    SeedQuery("infectious_disease", "sepsis management guidelines"),
    SeedQuery("infectious_disease", "antibiotic resistance gram negative"),
    SeedQuery("infectious_disease", "urinary tract infection treatment"),
    # Endocrinology
    SeedQuery("endocrinology", "type 2 diabetes glycemic control"),
    SeedQuery("endocrinology", "thyroid nodule management"),
    SeedQuery("endocrinology", "diabetic ketoacidosis management"),
    SeedQuery("endocrinology", "osteoporosis treatment postmenopausal"),
    # Psychiatry
    SeedQuery("psychiatry", "major depressive disorder antidepressant"),
    SeedQuery("psychiatry", "schizophrenia antipsychotic treatment"),
    SeedQuery("psychiatry", "bipolar disorder maintenance treatment"),
    SeedQuery("psychiatry", "generalized anxiety disorder treatment"),
]


async def run_seed(
    pool: DbPool,
    *,
    per_query: int,
    embed: bool,
    allow_llm: bool,
) -> dict[str, IngestStats]:
    """Ingest the whole plan through one shared, rate-limited NCBI client."""
    client = NcbiClient()
    results: dict[str, IngestStats] = {}
    try:
        for i, item in enumerate(SEED_PLAN, start=1):
            query = f"{item.query} AND hasabstract[Filter]"
            logger.info(
                "seed_query_start",
                index=i,
                total=len(SEED_PLAN),
                domain=item.domain,
                query=item.query,
            )
            stats = await run_ingest(
                pool,
                fetch_pubmed(client, query=query, limit=per_query),
                source="pubmed",
                query=item.query,
                allow_llm=allow_llm,
                # Embed once at the end, not per query.
                embed=False,
            )
            results[item.query] = stats
            print(json.dumps({"seed_progress": {item.query: stats.as_dict()}}), flush=True)
    finally:
        await client.close()

    if embed:
        from app.retrieval.embed import backfill_pending_chunks

        try:
            await backfill_pending_chunks(pool)
        except Exception as exc:
            logger.warning("seed_embedding_deferred", error=f"{type(exc).__name__}: {exc}")

    return results


async def _main(args: argparse.Namespace) -> int:
    if args.dry_run:
        for item in SEED_PLAN:
            print(f"{item.domain:20s} {item.query}")
        print(
            f"\n{len(SEED_PLAN)} queries x {args.per_query} = up to "
            f"{len(SEED_PLAN) * args.per_query} documents fetched"
        )
        return 0

    pool = await asyncpg.create_pool(
        dsn=get_settings().database_url,
        min_size=1,
        max_size=5,
        server_settings={"search_path": "public, extensions"},
    )
    assert pool is not None
    try:
        results = await run_seed(
            pool,
            per_query=args.per_query,
            embed=not args.no_embed,
            allow_llm=not args.no_llm,
        )
        async with pool.acquire() as conn:
            stats = await CorpusRepository().corpus_stats(conn)
    finally:
        await pool.close()

    totals = {
        "fetched": sum(s.fetched for s in results.values()),
        "inserted": sum(s.inserted for s in results.values()),
        "deduplicated": sum(s.deduplicated for s in results.values()),
        "skipped_no_text": sum(s.skipped_no_text for s in results.values()),
        "failed": sum(s.failed for s in results.values()),
    }
    print(json.dumps({"seed_totals": totals, "corpus": stats}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.ingestion.seed", description=__doc__)
    parser.add_argument("--per-query", type=int, default=600)
    parser.add_argument("--no-embed", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = parser.parse_args(argv)
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
