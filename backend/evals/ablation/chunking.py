"""Chunking ablation: four strategies, one labeled set, real recall@10.

Method
------
- Sample a fixed 200-document subset from the corpus (deterministic order).
- Re-fetch each document's structured sections from PubMed (disk-cached, so
  a committed run reproduces offline).
- Chunk every document with all four strategies.
- Embed each strategy's chunks with a single held-constant embedder (local
  lexical by default; ``--embedder cohere`` for the production embedder).
- Labeled set: query = document title, the one relevant document = its
  source. For each title, retrieve the top-k chunks from the pooled chunk set
  of all sampled documents; a hit means at least one chunk of the source
  document appears in the top-k.
- Report recall@10, recall@5, MRR@10, mean chunk size (tokens), chunk count.

The absolute numbers depend on the embedder (stamped in the output); the
*ranking* of strategies is what the ablation is for. Holding the embedder
constant makes it a fair comparison.

Run:
    uv run python -m evals.ablation.chunking --embedder local --sample 200
    uv run python -m evals.ablation.chunking --embedder cohere   # once keyed

Output: a markdown table on stdout and evals/results/chunking_ablation.json
(read by the Phase 13 methodology page — never hand-edited).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import asyncpg
import numpy as np

from app.config import get_settings
from app.ingestion.models import Chunk, RawDocument, RawSection
from app.ingestion.sources.ncbi import NcbiClient
from app.ingestion.sources.pubmed import parse_pubmed_article_set
from app.retrieval.chunking import STRATEGY_NAMES, get_strategy
from app.retrieval.embedders import TextEmbedder, get_embedder

if TYPE_CHECKING:
    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    DbPool = asyncpg.Pool

_HERE = Path(__file__).resolve().parent
_CACHE_DIR = _HERE / "cache"
_RESULTS = _HERE.parents[1] / "evals" / "results"
_RECALL_KS = (5, 10)


@dataclass(slots=True)
class StrategyResult:
    strategy: str
    documents: int
    total_chunks: int
    mean_chunk_tokens: float
    median_chunk_tokens: float
    recall_at_5: float
    recall_at_10: float
    mrr_at_10: float


@dataclass(slots=True)
class AblationReport:
    ablation: str
    generated_at: str
    embedder: str
    embedding_dimension: int
    sample_size: int
    corpus_documents: int
    strategies: list[StrategyResult] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "ablation": self.ablation,
                "generated_at": self.generated_at,
                "embedder": self.embedder,
                "embedding_dimension": self.embedding_dimension,
                "sample_size": self.sample_size,
                "corpus_documents": self.corpus_documents,
                "strategies": [asdict(s) for s in self.strategies],
            },
            indent=2,
        )


# -- corpus sampling + section re-fetch --------------------------------------------


async def _sample_pmids(pool: DbPool, *, want: int) -> list[str]:
    """A deterministic pseudo-random PMID sample (stable across runs)."""
    rows = await pool.fetch(
        "SELECT pmid FROM public.documents "
        "WHERE pmid IS NOT NULL AND source_type = 'pubmed' "
        "ORDER BY md5(id::text) LIMIT $1",
        want,
    )
    return [str(r["pmid"]) for r in rows]


def _cache_path(pmids: list[str]) -> Path:
    import hashlib

    key = hashlib.sha256(",".join(sorted(pmids)).encode()).hexdigest()[:16]
    return _CACHE_DIR / f"docs_{key}.json"


def _serialize(docs: list[RawDocument]) -> str:
    return json.dumps(
        [
            {
                "pmid": d.pmid,
                "title": d.title,
                "sections": [{"title": s.title, "content": s.content} for s in d.sections],
            }
            for d in docs
        ]
    )


def _deserialize(raw: str) -> list[RawDocument]:
    payload = json.loads(raw)
    docs: list[RawDocument] = []
    for item in payload:
        docs.append(
            RawDocument(
                source_type="pubmed",
                title=item["title"],
                pmid=item["pmid"],
                sections=[
                    RawSection(title=s["title"], content=s["content"]) for s in item["sections"]
                ],
            )
        )
    return docs


async def _load_documents(pmids: list[str], *, use_cache: bool) -> list[RawDocument]:
    """Fetch structured sections for the sample, caching to disk for offline reruns."""
    cache_file = _cache_path(pmids)
    if use_cache and cache_file.is_file():
        return _deserialize(cache_file.read_text(encoding="utf-8"))

    client = NcbiClient()
    documents: list[RawDocument] = []
    try:
        for batch in await client.fetch_xml_batches(db="pubmed", ids=pmids):
            documents.extend(parse_pubmed_article_set(batch))
    finally:
        await client.close()

    documents = [d for d in documents if d.full_text().strip()]
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(_serialize(documents), encoding="utf-8")
    return documents


# -- measurement --------------------------------------------------------------------


async def _chunk_all(strategy_name: str, documents: list[RawDocument]) -> list[list[Chunk]]:
    strategy = get_strategy(strategy_name)
    return [await strategy.chunk(document) for document in documents]


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normalized: np.ndarray = matrix / norms
    return normalized


async def score_strategy(
    embedder: TextEmbedder,
    documents: list[RawDocument],
    chunks_per_doc: list[list[Chunk]],
    *,
    strategy_name: str,
) -> StrategyResult:
    """Embed a strategy's chunks and measure retrieval of the source document."""
    chunk_texts: list[str] = []
    chunk_doc_index: list[int] = []
    token_counts: list[int] = []
    for doc_index, chunks in enumerate(chunks_per_doc):
        for chunk in chunks:
            chunk_texts.append(chunk.embedding_input)
            chunk_doc_index.append(doc_index)
            token_counts.append(chunk.token_count)

    if not chunk_texts:
        return StrategyResult(strategy_name, len(documents), 0, 0.0, 0.0, 0.0, 0.0, 0.0)

    chunk_vectors = _normalize(
        np.array(await embedder.embed_documents(chunk_texts), dtype=np.float64)
    )
    query_vectors = _normalize(
        np.array(await embedder.embed_queries([d.title for d in documents]), dtype=np.float64)
    )
    source_of_chunk = np.array(chunk_doc_index)

    # scores[q, c] = cosine(query q, chunk c)
    scores = query_vectors @ chunk_vectors.T
    top_k = min(max(_RECALL_KS), scores.shape[1])
    # Indices of the top-k chunks per query, best first.
    top_idx = np.argsort(-scores, axis=1)[:, :top_k]

    hits_at = dict.fromkeys(_RECALL_KS, 0)
    reciprocal_ranks = 0.0
    for query_index in range(len(documents)):
        ranked_docs = source_of_chunk[top_idx[query_index]]
        first_hit_rank = None
        for rank, doc_index in enumerate(ranked_docs, start=1):
            if doc_index == query_index:
                first_hit_rank = rank
                break
        for k in _RECALL_KS:
            if first_hit_rank is not None and first_hit_rank <= k:
                hits_at[k] += 1
        if first_hit_rank is not None and first_hit_rank <= 10:
            reciprocal_ranks += 1.0 / first_hit_rank

    n = len(documents)
    tokens = np.array(token_counts)
    return StrategyResult(
        strategy=strategy_name,
        documents=n,
        total_chunks=len(chunk_texts),
        mean_chunk_tokens=round(float(tokens.mean()), 1),
        median_chunk_tokens=round(float(np.median(tokens)), 1),
        recall_at_5=round(hits_at[5] / n, 4),
        recall_at_10=round(hits_at[10] / n, 4),
        mrr_at_10=round(reciprocal_ranks / n, 4),
    )


async def run_ablation(
    documents: list[RawDocument], *, embedder_name: str, corpus_documents: int
) -> AblationReport:
    embedder = get_embedder(embedder_name)
    report = AblationReport(
        ablation="chunking_strategies",
        generated_at=datetime.now(tz=UTC).isoformat(),
        embedder=embedder.name,
        embedding_dimension=embedder.dimension,
        sample_size=len(documents),
        corpus_documents=corpus_documents,
    )
    for strategy_name in STRATEGY_NAMES:
        chunks_per_doc = await _chunk_all(strategy_name, documents)
        result = await score_strategy(
            embedder, documents, chunks_per_doc, strategy_name=strategy_name
        )
        report.strategies.append(result)
    return report


def render_markdown(report: AblationReport) -> str:
    lines = [
        f"# Chunking ablation — recall@10 on {report.sample_size} documents",
        "",
        f"Embedder: `{report.embedder}` ({report.embedding_dimension}-dim) · "
        f"generated {report.generated_at}",
        "",
        "| Strategy | Chunks | Mean tokens | Median tokens | Recall@5 | Recall@10 | MRR@10 |",
        "|----------|-------:|------------:|--------------:|---------:|----------:|-------:|",
    ]
    best = max((s.recall_at_10 for s in report.strategies), default=0.0)
    for s in report.strategies:
        marker = " (best)" if s.recall_at_10 == best and best > 0 else ""
        lines.append(
            f"| {s.strategy}{marker} | {s.total_chunks} | {s.mean_chunk_tokens} | "
            f"{s.median_chunk_tokens} | {s.recall_at_5:.3f} | {s.recall_at_10:.3f} | "
            f"{s.mrr_at_10:.3f} |"
        )
    return "\n".join(lines)


async def _main(args: argparse.Namespace) -> int:
    pool = await asyncpg.create_pool(
        dsn=get_settings().database_url,
        min_size=1,
        max_size=3,
        server_settings={"search_path": "public, extensions"},
    )
    assert pool is not None
    try:
        corpus_documents = int(await pool.fetchval("SELECT count(*) FROM public.documents") or 0)
        pmids = await _sample_pmids(pool, want=args.fetch)
    finally:
        await pool.close()

    documents = await _load_documents(pmids, use_cache=not args.no_cache)
    documents = documents[: args.sample]
    if len(documents) < args.sample:
        print(f"warning: only {len(documents)} usable documents (wanted {args.sample})")

    report = await run_ablation(
        documents, embedder_name=args.embedder, corpus_documents=corpus_documents
    )

    print(render_markdown(report))
    _RESULTS.mkdir(parents=True, exist_ok=True)
    out = _RESULTS / "chunking_ablation.json"
    out.write_text(report.to_json() + "\n", encoding="utf-8")
    print(f"\nwrote {out.relative_to(_HERE.parents[1])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.ablation.chunking", description=__doc__)
    parser.add_argument("--embedder", default="local", help="local | cohere")
    parser.add_argument("--sample", type=int, default=200, help="documents to evaluate")
    parser.add_argument(
        "--fetch", type=int, default=260, help="PMIDs to pull (buffer for unusable records)"
    )
    parser.add_argument("--no-cache", action="store_true", help="ignore the on-disk section cache")
    args = parser.parse_args(argv)
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
