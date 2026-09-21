"""A corpus snapshot for CI: the golden set's relevant documents plus a
deterministic sample of distractors, with their structural chunks and the
original ids, so the golden labels resolve in a fresh database.

    uv run python -m evals.golden.snapshot export   # -> evals/golden/snapshot/*.jsonl.gz
    uv run python -m evals.golden.snapshot load     # into DATABASE_URL, then embed

CI loads the snapshot into its Postgres service, embeds it with the local
embedder (deterministic, no key) and runs the golden set against it. The
numbers differ from a full-corpus run — fewer distractors — so the CI
baseline (``evals/results/golden_ci.json``) is produced the same way, and
the regression check compares like with like.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg

from app.config import get_settings
from app.core.deps import DbPool
from app.repositories.corpus import CorpusRepository
from app.retrieval.embed import backfill_pending_chunks
from evals.golden.schema import SET_PATH, load_set

SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshot"
DOCUMENTS = SNAPSHOT_DIR / "documents.jsonl.gz"
CHUNKS = SNAPSHOT_DIR / "chunks.jsonl.gz"
DISTRACTORS = 1200
SEED = "golden-snapshot-v1"


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return json.loads(value) if value[:1] in "[{" else value
        except json.JSONDecodeError:
            return value
    return value


async def export(pool: DbPool, set_path: Path) -> dict[str, int]:
    items = load_set(set_path)
    relevant = sorted({d for i in items for d in i.relevant_document_ids})
    async with pool.acquire() as conn:
        distractors = await conn.fetch(
            """
            SELECT id::text AS id FROM public.documents
            WHERE org_id IS NULL AND id <> ALL($1::uuid[])
              AND jsonb_array_length(coalesce(metadata->'mesh_terms', '[]'::jsonb)) > 0
            ORDER BY md5($2 || id::text)
            LIMIT $3
            """,
            [UUID(d) for d in relevant],
            SEED,
            DISTRACTORS,
        )
        ids = [UUID(d) for d in relevant] + [UUID(str(r["id"])) for r in distractors]
        docs = await conn.fetch(
            """
            SELECT id, source_type, external_id, title, abstract, authors, journal,
                   publication_date, doi, pmid, url, study_type, evidence_grade,
                   content_hash, metadata, classification, ingested_at
            FROM public.documents WHERE id = ANY($1::uuid[]) ORDER BY id
            """,
            ids,
        )
        chunks = await conn.fetch(
            """
            SELECT id, document_id, chunk_index, content, token_count, section, strategy,
                   embed_text, metadata
            FROM public.chunks
            WHERE strategy = 'structural' AND document_id = ANY($1::uuid[])
            ORDER BY document_id, chunk_index
            """,
            ids,
        )
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    with gzip.open(DOCUMENTS, "wt", encoding="utf-8") as fh:
        for row in docs:
            fh.write(json.dumps({k: _jsonable(v) for k, v in dict(row).items()}) + "\n")
    with gzip.open(CHUNKS, "wt", encoding="utf-8") as fh:
        for row in chunks:
            fh.write(json.dumps({k: _jsonable(v) for k, v in dict(row).items()}) + "\n")
    return {"documents": len(docs), "chunks": len(chunks), "relevant": len(relevant)}


def _read(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


async def load(pool: DbPool, *, embedder_name: str) -> dict[str, int]:
    docs = _read(DOCUMENTS)
    chunks = _read(CHUNKS)
    async with pool.acquire() as conn, conn.transaction():
        await conn.executemany(
            """
            INSERT INTO public.documents (
                id, org_id, source_type, external_id, title, abstract, authors, journal,
                publication_date, doi, pmid, url, study_type, evidence_grade, content_hash,
                metadata, classification, ingested_at
            ) VALUES ($1, NULL, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11, $12, $13,
                      $14, $15::jsonb, $16::jsonb, $17)
            ON CONFLICT (id) DO NOTHING
            """,
            [
                (
                    UUID(d["id"]),
                    d["source_type"],
                    d["external_id"],
                    d["title"],
                    d["abstract"],
                    json.dumps(d["authors"]),
                    d["journal"],
                    date.fromisoformat(d["publication_date"]) if d["publication_date"] else None,
                    d["doi"],
                    d["pmid"],
                    d["url"],
                    d["study_type"],
                    d["evidence_grade"],
                    d["content_hash"],
                    json.dumps(d["metadata"]),
                    json.dumps(d["classification"]),
                    datetime.fromisoformat(d["ingested_at"]),
                )
                for d in docs
            ],
        )
        await conn.executemany(
            """
            INSERT INTO public.chunks (
                id, document_id, chunk_index, content, token_count, section, strategy,
                embed_text, metadata
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            [
                (
                    UUID(c["id"]),
                    UUID(c["document_id"]),
                    c["chunk_index"],
                    c["content"],
                    c["token_count"],
                    c["section"],
                    c["strategy"],
                    c["embed_text"],
                    json.dumps(c["metadata"]),
                )
                for c in chunks
            ],
        )
    embedded = await backfill_pending_chunks(pool, embedder_name=embedder_name)
    async with pool.acquire() as conn:
        await CorpusRepository().rebuild_lexeme_stats(conn, strategy="structural")
        await conn.execute(
            """
            INSERT INTO public.mesh_terms (term, document_count)
            SELECT lower(term), count(*) FROM public.documents d,
                   jsonb_array_elements_text(d.metadata->'mesh_terms') AS term
            WHERE d.org_id IS NULL GROUP BY 1
            ON CONFLICT (term) DO UPDATE SET document_count = excluded.document_count
            """
        )
    return {"documents": len(docs), "chunks": len(chunks), "embedded": embedded}


async def _main(args: argparse.Namespace) -> int:
    pool = await asyncpg.create_pool(
        get_settings().database_url,
        min_size=1,
        max_size=2,
        # Supabase keeps extensions in their own schema, so `vector` is only
        # resolvable with it on the path — this loader is what a first deploy
        # runs to get a corpus into a fresh Supabase project.
        server_settings={"search_path": "public, extensions"},
    )
    try:
        if args.command == "export":
            stats = await export(pool, args.set)
        else:
            stats = await load(pool, embedder_name=args.embedder)
    finally:
        await pool.close()
    print(json.dumps(stats))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export")
    exp.add_argument("--set", type=Path, default=SET_PATH)
    ld = sub.add_parser("load")
    ld.add_argument("--embedder", default="local")
    args = parser.parse_args(argv)
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["CHUNKS", "DOCUMENTS", "SNAPSHOT_DIR", "export", "load"]
