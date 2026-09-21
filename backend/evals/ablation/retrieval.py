"""Retrieval ablation — the project's most important artifact.

Measures recall@10, MRR, and nDCG@10 at each stage of the pipeline:

    dense-only → lexical-only → RRF-fused → reranked → boosted

Golden set (known-item retrieval): a deterministic sample of corpus documents;
the query is each document's title, the single relevant document is its source.
A stage "hits" when the source document appears in the top-k (documents ranked
by their best chunk). This has no human labels but is honest, reproducible, and
sensitive to every stage — a better ranking finds the paper faster.

Absolute numbers depend on the embedder + reranker (both stamped in the
output); the *shape* across stages is the point. Run with ``--embedder cohere
--reranker cohere`` once keyed for the production numbers.

    uv run python -m evals.ablation.retrieval --sample 100

Writes a markdown table to stdout and evals/results/retrieval_ablation.json
(read by the Phase 13 methodology page — never hand-edited).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import asyncpg

from app.config import get_settings
from app.retrieval.boost import apply_boosts
from app.retrieval.dense import dense_search
from app.retrieval.embed import EmbeddingService
from app.retrieval.embedders import get_embedder
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.lexical import lexical_search, load_synonyms
from app.retrieval.pipeline import RetrievalConfig, RetrievalPipeline
from app.retrieval.rerank import get_reranker
from app.retrieval.types import RetrievedChunk

if TYPE_CHECKING:
    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    DbPool = asyncpg.Pool

_RESULTS = Path(__file__).resolve().parents[1] / "results"
_STAGES = ("dense", "lexical", "fused", "reranked", "boosted")
_CANDIDATES = 50


@dataclass(slots=True)
class StageMetrics:
    stage: str
    recall_at_10: float
    mrr: float
    ndcg_at_10: float


@dataclass(slots=True)
class RetrievalReport:
    ablation: str
    generated_at: str
    embedder: str
    reranker: str
    sample_size: int
    corpus_documents: int
    corpus_chunks: int
    stages: list[StageMetrics] = field(default_factory=list)
    latency_ms: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "ablation": self.ablation,
                "generated_at": self.generated_at,
                "embedder": self.embedder,
                "reranker": self.reranker,
                "sample_size": self.sample_size,
                "corpus_documents": self.corpus_documents,
                "corpus_chunks": self.corpus_chunks,
                "stages": [asdict(s) for s in self.stages],
                "latency_ms": self.latency_ms,
            },
            indent=2,
        )


# -- metrics (single relevant document, binary relevance) ----------------------------


def _rank_of_source(ranked_chunks: list[RetrievedChunk], source: UUID) -> int | None:
    """1-based rank of the source document (documents ranked by best chunk)."""
    seen: set[UUID] = set()
    rank = 0
    for chunk in ranked_chunks:
        if chunk.document_id in seen:
            continue
        seen.add(chunk.document_id)
        rank += 1
        if chunk.document_id == source:
            return rank
    return None


def _dcg(rank: int | None, k: int) -> float:
    if rank is None or rank > k:
        return 0.0
    return 1.0 / math.log2(rank + 1)  # ideal DCG is 1.0 (rank 1), so nDCG == DCG


# -- golden set ----------------------------------------------------------------------


@dataclass(slots=True)
class GoldenItem:
    query: str
    source_id: UUID


async def sample_golden(pool: DbPool, *, sample: int, strategy: str) -> list[GoldenItem]:
    """Deterministic known-item golden set: title → source document."""
    rows = await pool.fetch(
        """
        SELECT d.id, d.title
        FROM public.documents d
        WHERE d.title IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM public.chunks c
            JOIN public.chunk_embeddings e ON e.chunk_id = c.id
            WHERE c.document_id = d.id AND c.strategy = $1
          )
        ORDER BY md5(d.id::text)
        LIMIT $2
        """,
        strategy,
        sample,
    )
    return [GoldenItem(query=str(r["title"]), source_id=r["id"]) for r in rows]


# -- ablation ------------------------------------------------------------------------


async def run_retrieval_ablation(
    pool: DbPool,
    *,
    embedder_name: str,
    reranker_name: str,
    sample: int,
    strategy: str = "structural",
) -> RetrievalReport:
    embedder = EmbeddingService(get_embedder(embedder_name))
    reranker = get_reranker(reranker_name)
    async with pool.acquire() as conn:
        synonyms = await load_synonyms(conn)
        corpus_documents = int(await conn.fetchval("SELECT count(*) FROM public.documents") or 0)
        corpus_chunks = int(
            await conn.fetchval(
                "SELECT count(*) FROM public.chunk_embeddings WHERE strategy = $1",
                strategy,
            )
            or 0
        )
    golden = await sample_golden(pool, sample=sample, strategy=strategy)

    ranks: dict[str, list[int | None]] = {stage: [] for stage in _STAGES}
    for item in golden:
        query_vector = await embedder.embed_query(item.query)
        async with pool.acquire() as conn:
            dense = await dense_search(
                conn, query_vector=query_vector, org_id=None, strategy=strategy, limit=_CANDIDATES
            )
            lexical = await lexical_search(
                conn,
                query_text=item.query,
                org_id=None,
                strategy=strategy,
                limit=_CANDIDATES,
                synonyms=synonyms,
            )
        fused = reciprocal_rank_fusion(dense, lexical, limit=_CANDIDATES)
        reranked = await reranker.rerank(item.query, fused, top_n=_CANDIDATES)
        boosted = apply_boosts([_clone(c) for c in reranked])

        for stage, chunks in (
            ("dense", dense),
            ("lexical", lexical),
            ("fused", fused),
            ("reranked", reranked),
            ("boosted", boosted),
        ):
            ranks[stage].append(_rank_of_source(chunks, item.source_id))

    stages = [
        StageMetrics(
            stage=stage,
            recall_at_10=_mean(1.0 if r is not None and r <= 10 else 0.0 for r in rank_list),
            mrr=_mean(1.0 / r if r is not None else 0.0 for r in rank_list),
            ndcg_at_10=_mean(_dcg(r, 10) for r in rank_list),
        )
        for stage, rank_list in ranks.items()
    ]

    latency = await _measure_latency(pool, embedder, reranker_name, golden, strategy)

    return RetrievalReport(
        ablation="retrieval_stages",
        generated_at=datetime.now(tz=UTC).isoformat(),
        embedder=embedder.embedder_name,
        reranker=reranker.name,
        sample_size=len(golden),
        corpus_documents=corpus_documents,
        corpus_chunks=corpus_chunks,
        stages=stages,
        latency_ms=latency,
    )


def _clone(chunk: RetrievedChunk) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        content=chunk.content,
        section=chunk.section,
        title=chunk.title,
        publication_date=chunk.publication_date,
        evidence_grade=chunk.evidence_grade,
        study_type=chunk.study_type,
        score=chunk.score,
        components=dict(chunk.components),
    )


async def _measure_latency(
    pool: DbPool,
    embedder: EmbeddingService,
    reranker_name: str,
    golden: list[GoldenItem],
    strategy: str,
) -> dict[str, float]:
    """End-to-end pipeline latency (production config, rerank top-8) per query."""
    pipeline = RetrievalPipeline(
        pool, embedder, get_reranker(reranker_name), RetrievalConfig(strategy=strategy)
    )
    durations: list[float] = []
    for item in golden:
        result = await pipeline.retrieve(item.query)
        durations.append(result.total_ms)
    durations.sort()
    return {
        "p50": round(statistics.median(durations), 2),
        "p95": round(durations[max(0, math.ceil(0.95 * len(durations)) - 1)], 2),
        "max": round(durations[-1], 2),
    }


def _mean(values: object) -> float:
    data = list(values)  # type: ignore[call-overload]
    return round(sum(data) / len(data), 4) if data else 0.0


def render_markdown(report: RetrievalReport) -> str:
    lines = [
        f"# Retrieval ablation — {report.sample_size} known-item queries over "
        f"{report.corpus_documents} documents ({report.corpus_chunks} chunks)",
        "",
        f"Embedder: `{report.embedder}` · Reranker: `{report.reranker}` · "
        f"generated {report.generated_at}",
        "",
        "| Stage | Recall@10 | MRR | nDCG@10 |",
        "|-------|----------:|----:|--------:|",
    ]
    for s in report.stages:
        lines.append(f"| {s.stage} | {s.recall_at_10:.3f} | {s.mrr:.3f} | {s.ndcg_at_10:.3f} |")
    lines += [
        "",
        f"End-to-end latency (production config): p50 {report.latency_ms.get('p50')} ms · "
        f"**p95 {report.latency_ms.get('p95')} ms** · max {report.latency_ms.get('max')} ms",
    ]
    return "\n".join(lines)


async def _main(args: argparse.Namespace) -> int:
    pool = await asyncpg.create_pool(
        dsn=get_settings().database_url,
        min_size=1,
        max_size=5,
        server_settings={"search_path": "public, extensions"},
    )
    assert pool is not None
    try:
        report = await run_retrieval_ablation(
            pool,
            embedder_name=args.embedder,
            reranker_name=args.reranker,
            sample=args.sample,
        )
    finally:
        await pool.close()

    print(render_markdown(report))
    _RESULTS.mkdir(parents=True, exist_ok=True)
    out = _RESULTS / "retrieval_ablation.json"
    out.write_text(report.to_json() + "\n", encoding="utf-8")
    print(f"\nwrote {out.relative_to(Path(__file__).resolve().parents[2])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.ablation.retrieval", description=__doc__)
    parser.add_argument("--embedder", default="local", help="local | cohere")
    parser.add_argument("--reranker", default="local", help="local | cohere")
    parser.add_argument("--sample", type=int, default=100)
    args = parser.parse_args(argv)
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
