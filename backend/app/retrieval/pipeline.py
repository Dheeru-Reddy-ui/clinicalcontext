"""Hybrid retrieval orchestration.

    embed query → (dense ∥ lexical) → RRF fuse → rerank → boost

Each stage is timed; when a ``query_id`` is supplied, one ``retrieval_traces``
row is written per stage (chunk ids + scores + duration) so any retrieval can
be inspected after the fact. Dense and lexical run concurrently on separate
pooled connections.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

import anyio
import structlog

from app.core.telemetry import span
from app.repositories.retrieval import RetrievalRepository
from app.retrieval.boost import BoostConfig, apply_boosts
from app.retrieval.dense import dense_search
from app.retrieval.embed import EmbeddingService
from app.retrieval.fusion import FusionWeights, reciprocal_rank_fusion
from app.retrieval.lexical import lexical_search, load_synonyms
from app.retrieval.rerank import Reranker
from app.retrieval.types import RetrievedChunk, StageTiming
from app.services import cost

if TYPE_CHECKING:
    import asyncpg

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("app.retrieval.pipeline")


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    strategy: str = "structural"
    dense_limit: int = 50
    lexical_limit: int = 50
    fused_limit: int = 50
    rerank_top_n: int = 8
    fusion_weights: FusionWeights = field(default_factory=FusionWeights)
    boost: BoostConfig = field(default_factory=BoostConfig)
    # Stage switches. Production runs every stage; the ablation runner turns
    # them off one at a time to measure what each one buys (dense-only is
    # the baseline). Off stages still record a snapshot so the waterfall and
    # traces keep their shape.
    lexical: bool = True
    rerank: bool = True
    boosts: bool = True


@dataclass(slots=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    timings: list[StageTiming]
    # True end-to-end wall-clock. NOT the sum of stage durations: dense and
    # lexical run concurrently, so summing would double-count their overlap.
    wall_ms: float = 0.0
    # Per-stage snapshots (used by the ablation; the app reads `chunks`).
    stages: dict[str, list[RetrievedChunk]] = field(default_factory=dict)

    @property
    def total_ms(self) -> float:
        """End-to-end latency (alias for wall_ms)."""
        return self.wall_ms

    @property
    def sum_stage_ms(self) -> float:
        """Sum of per-stage durations — overcounts concurrent stages; for a
        breakdown view only, not latency."""
        return round(sum(t.duration_ms for t in self.timings), 2)


class RetrievalPipeline:
    def __init__(
        self,
        pool: DbPool,
        embedder: EmbeddingService,
        reranker: Reranker,
        config: RetrievalConfig | None = None,
    ) -> None:
        self._pool = pool
        self._embedder = embedder
        self._reranker = reranker
        self._config = config if config is not None else RetrievalConfig()
        self._synonyms: dict[str, list[str]] | None = None

    async def _get_synonyms(self) -> dict[str, list[str]]:
        if self._synonyms is None:
            async with self._pool.acquire() as conn:
                self._synonyms = await load_synonyms(conn)
        return self._synonyms

    async def retrieve(
        self,
        query: str,
        *,
        org_id: UUID | None = None,
        query_id: UUID | None = None,
        keep_stage_snapshots: bool = False,
    ) -> RetrievalResult:
        config = self._config
        pipeline_started = time.perf_counter()
        timings: list[StageTiming] = []
        stages: dict[str, list[RetrievedChunk]] = {}

        def record(stage: str, chunks: list[RetrievedChunk], started: float) -> None:
            timings.append(
                StageTiming(stage, round((time.perf_counter() - started) * 1000, 2), len(chunks))
            )
            if keep_stage_snapshots:
                stages[stage] = list(chunks)

        # 1. Embed the query (search_query input type).
        started = time.perf_counter()
        with span("retrieval.embed_query", embedder=self._embedder.embedder_name):
            query_vector = await self._embedder.embed_query(query)
        embed_ms = round((time.perf_counter() - started) * 1000, 2)
        timings.append(StageTiming("embed_query", embed_ms, 1))

        synonyms = await self._get_synonyms()

        # 2. Dense ∥ lexical, concurrently on separate connections.
        dense_result: list[RetrievedChunk] = []
        lexical_result: list[RetrievedChunk] = []

        async def run_dense() -> None:
            nonlocal dense_result
            start = time.perf_counter()
            async with self._pool.acquire() as conn:
                with span("retrieval.dense", limit=config.dense_limit):
                    dense_result = await dense_search(
                        conn,
                        query_vector=query_vector,
                        org_id=org_id,
                        strategy=config.strategy,
                        limit=config.dense_limit,
                    )
            record("dense", dense_result, start)

        async def run_lexical() -> None:
            nonlocal lexical_result
            start = time.perf_counter()
            if not config.lexical:
                record("lexical", [], start)
                return
            async with self._pool.acquire() as conn:
                with span("retrieval.lexical", limit=config.lexical_limit):
                    lexical_result = await lexical_search(
                        conn,
                        query_text=query,
                        org_id=org_id,
                        strategy=config.strategy,
                        limit=config.lexical_limit,
                        synonyms=synonyms,
                    )
            record("lexical", lexical_result, start)

        async with anyio.create_task_group() as tg:
            tg.start_soon(run_dense)
            tg.start_soon(run_lexical)

        # 3. Fuse (dense-only keeps its cosine scores: nothing to fuse with).
        start = time.perf_counter()
        if config.lexical:
            fused = reciprocal_rank_fusion(
                dense_result,
                lexical_result,
                weights=config.fusion_weights,
                limit=config.fused_limit,
            )
        else:
            fused = dense_result[: config.fused_limit]
        record("fused", fused, start)

        # 4. Rerank the fused candidates down to the final N.
        start = time.perf_counter()
        if config.rerank:
            with span(
                "retrieval.rerank",
                reranker=self._reranker.name,
                candidates=len(fused),
                top_n=config.rerank_top_n,
            ):
                reranked = await self._reranker.rerank(query, fused, top_n=config.rerank_top_n)
            cost.record(
                "rerank",
                provider="cohere" if self._reranker.name.startswith("cohere") else "local",
                model=self._reranker.name.removeprefix("cohere-"),
                units=1,
                unit="searches",
            )
        else:
            reranked = fused[: config.rerank_top_n]
        record("reranked", reranked, start)

        # 5. Boost (recency + evidence grade), transparently.
        start = time.perf_counter()
        boosted = apply_boosts(reranked, config=config.boost) if config.boosts else reranked
        record("boosted", boosted, start)

        wall_ms = round((time.perf_counter() - pipeline_started) * 1000, 2)

        if query_id is not None and org_id is not None:
            await self._write_traces(
                query_id, org_id, timings, dense_result, lexical_result, fused, reranked, boosted
            )

        return RetrievalResult(chunks=boosted, timings=timings, wall_ms=wall_ms, stages=stages)

    async def _write_traces(
        self,
        query_id: UUID,
        org_id: UUID,
        timings: list[StageTiming],
        dense: list[RetrievedChunk],
        lexical: list[RetrievedChunk],
        fused: list[RetrievedChunk],
        reranked: list[RetrievedChunk],
        boosted: list[RetrievedChunk],
    ) -> None:
        by_stage = {t.stage: t.duration_ms for t in timings}
        repo = RetrievalRepository()
        snapshots = {
            "dense": dense,
            "lexical": lexical,
            "fused": fused,
            "reranked": reranked,
            "boosted": boosted,
        }
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                for stage, chunks in snapshots.items():
                    await repo.insert_stage_trace(
                        conn,
                        query_id=query_id,
                        org_id=org_id,
                        stage=stage,
                        chunks=chunks,
                        duration_ms=by_stage.get(stage, 0.0),
                    )
        except Exception as exc:  # tracing is observability, never fatal to a query
            logger.warning("retrieval_trace_write_failed", error=f"{type(exc).__name__}: {exc}")
