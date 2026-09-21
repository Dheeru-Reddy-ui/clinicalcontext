"""Reciprocal Rank Fusion of the dense and lexical result lists.

RRF combines rankings by summing ``weight / (k + rank)`` across lists, so a
chunk ranked highly by either retriever surfaces without needing the two
score scales to be comparable. ``k=60`` is the standard constant; the
dense/lexical weights are configurable.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.retrieval.types import RetrievedChunk

RRF_K = 60


@dataclass(frozen=True, slots=True)
class FusionWeights:
    dense: float = 1.0
    lexical: float = 1.0


def reciprocal_rank_fusion(
    dense: list[RetrievedChunk],
    lexical: list[RetrievedChunk],
    *,
    k: int = RRF_K,
    weights: FusionWeights | None = None,
    limit: int | None = None,
) -> list[RetrievedChunk]:
    """Fuse two ranked lists into one, de-duplicated by chunk id.

    The surviving object keeps each retriever's rank and contribution in
    ``components`` (``dense_rank``/``lexical_rank``/``rrf``) for transparency.
    """
    resolved = weights if weights is not None else FusionWeights()
    merged: dict[str, RetrievedChunk] = {}
    rrf_score: dict[str, float] = {}

    def contribute(chunks: list[RetrievedChunk], weight: float, rank_label: str) -> None:
        for rank, chunk in enumerate(chunks, start=1):
            key = str(chunk.chunk_id)
            existing = merged.get(key)
            if existing is None:
                merged[key] = chunk
                existing = chunk
                rrf_score[key] = 0.0
            # Preserve the per-retriever score already on the chunk.
            existing.components.setdefault(rank_label.replace("_rank", ""), chunk.score)
            existing.components[rank_label] = float(rank)
            rrf_score[key] += weight / (k + rank)

    contribute(dense, resolved.dense, "dense_rank")
    contribute(lexical, resolved.lexical, "lexical_rank")

    fused = list(merged.values())
    for chunk in fused:
        chunk.with_score("rrf", rrf_score[str(chunk.chunk_id)])
    fused.sort(key=lambda c: c.score, reverse=True)
    return fused[:limit] if limit is not None else fused
