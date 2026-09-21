"""Corpus ingestion CLI.

python -m app.ingestion.cli ingest --source pubmed --query "..." --limit 500
python -m app.ingestion.cli ingest --source guideline --file path.pdf --title "..."
python -m app.ingestion.cli embed-pending [--limit N]
python -m app.ingestion.cli enqueue --source pubmed --query "..." --limit 500
python -m app.ingestion.cli stats
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import TYPE_CHECKING

import asyncpg
from arq import create_pool
from arq.connections import RedisSettings

from app.config import get_settings
from app.ingestion.models import IngestStats
from app.ingestion.pipeline import ingest_from_source
from app.repositories.corpus import CorpusRepository
from app.retrieval.embed import backfill_pending_chunks

if TYPE_CHECKING:
    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    DbPool = asyncpg.Pool


async def _open_pool() -> DbPool:
    pool = await asyncpg.create_pool(
        dsn=get_settings().database_url,
        min_size=1,
        max_size=5,
        server_settings={"search_path": "public, extensions"},
    )
    assert pool is not None
    return pool


async def _cmd_ingest(args: argparse.Namespace) -> int:
    pool = await _open_pool()

    async def print_progress(stats: IngestStats) -> None:
        print(json.dumps({"progress": stats.as_dict()}), flush=True)

    try:
        stats = await ingest_from_source(
            pool,
            source=args.source,
            query=args.query,
            limit=args.limit,
            file_path=args.file,
            title=args.title,
            allow_llm=not args.no_llm,
            embed=not args.no_embed,
            progress_cb=print_progress,
        )
    finally:
        await pool.close()
    print(json.dumps({"result": stats.as_dict()}, indent=2))
    return 1 if stats.fetched > 0 and stats.inserted + stats.deduplicated == 0 else 0


async def _cmd_embed_pending(args: argparse.Namespace) -> int:
    pool = await _open_pool()
    try:
        embedded = await backfill_pending_chunks(
            pool, embedder_name=args.embedder, limit=args.limit
        )
        async with pool.acquire() as conn:
            remaining = await CorpusRepository().count_chunks_pending_embedding(conn)
    finally:
        await pool.close()
    print(json.dumps({"embedded": embedded, "still_pending": remaining}))
    return 0


async def _cmd_classify_pending(args: argparse.Namespace) -> int:
    from app.ingestion.classifier import backfill_classifications

    pool = await _open_pool()
    try:
        reclassified = await backfill_classifications(pool, limit=args.limit)
        async with pool.acquire() as conn:
            remaining = await CorpusRepository().count_documents_pending_classification(conn)
    finally:
        await pool.close()
    print(json.dumps({"reclassified": reclassified, "still_pending": remaining}))
    return 0


async def _cmd_rechunk(args: argparse.Namespace) -> int:
    from app.ingestion.rechunk import rechunk_corpus

    pool = await _open_pool()
    try:
        stats = await rechunk_corpus(
            pool, strategy=args.strategy, embedder_name=args.embedder, limit=args.limit
        )
    finally:
        await pool.close()
    print(json.dumps(stats))
    return 0


async def _cmd_build_lexeme_stats(args: argparse.Namespace) -> int:
    pool = await _open_pool()
    try:
        async with pool.acquire() as conn:
            count = await CorpusRepository().rebuild_lexeme_stats(conn)
    finally:
        await pool.close()
    print(json.dumps({"lexemes": count}))
    return 0


async def _cmd_enqueue(args: argparse.Namespace) -> int:
    redis = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        job = await redis.enqueue_job(
            "ingest_job", source=args.source, query=args.query, limit=args.limit
        )
        assert job is not None
        print(json.dumps({"job_id": job.job_id, "status": "enqueued"}))
    finally:
        await redis.aclose()
    return 0


async def _cmd_stats(_: argparse.Namespace) -> int:
    pool = await _open_pool()
    try:
        async with pool.acquire() as conn:
            stats = await CorpusRepository().corpus_stats(conn)
    finally:
        await pool.close()
    print(json.dumps(stats, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.ingestion.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="run an ingest in-process")
    ingest.add_argument("--source", required=True, choices=["pubmed", "pmc", "guideline"])
    ingest.add_argument("--query", help="search query (pubmed/pmc)")
    ingest.add_argument("--limit", type=int, default=100)
    ingest.add_argument("--file", help="PDF path (guideline)")
    ingest.add_argument("--title", help="document title override (guideline)")
    ingest.add_argument("--no-embed", action="store_true", help="skip the embedding stage")
    ingest.add_argument("--no-llm", action="store_true", help="skip the LLM classifier fallback")
    ingest.set_defaults(handler=_cmd_ingest)

    embed = sub.add_parser("embed-pending", help="backfill embeddings for pending chunks")
    embed.add_argument("--limit", type=int, default=None)
    embed.add_argument("--embedder", default="cohere", help="cohere (production) | local (offline)")
    embed.set_defaults(handler=_cmd_embed_pending)

    classify = sub.add_parser(
        "classify-pending", help="LLM-classify documents left ungraded at ingest"
    )
    classify.add_argument("--limit", type=int, default=None)
    classify.set_defaults(handler=_cmd_classify_pending)

    rechunk = sub.add_parser(
        "rechunk",
        help="chunk the shared corpus with a second strategy (kept alongside structural)",
    )
    rechunk.add_argument("--strategy", required=True, choices=["fixed", "recursive", "semantic"])
    rechunk.add_argument("--embedder", default="cohere", help="cohere (production) | local")
    rechunk.add_argument("--limit", type=int, default=None, help="documents to process")
    rechunk.set_defaults(handler=_cmd_rechunk)

    lexemes = sub.add_parser(
        "build-lexeme-stats", help="recompute lexeme frequencies for lexical pruning"
    )
    lexemes.set_defaults(handler=_cmd_build_lexeme_stats)

    enqueue = sub.add_parser("enqueue", help="enqueue an ingest job on the arq worker")
    enqueue.add_argument("--source", required=True, choices=["pubmed", "pmc"])
    enqueue.add_argument("--query", required=True)
    enqueue.add_argument("--limit", type=int, default=100)
    enqueue.set_defaults(handler=_cmd_enqueue)

    stats = sub.add_parser("stats", help="corpus counts by source/grade/study type")
    stats.set_defaults(handler=_cmd_stats)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = asyncio.run(args.handler(args))
    return int(result)


if __name__ == "__main__":
    sys.exit(main())
