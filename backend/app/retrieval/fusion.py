"""Reciprocal Rank Fusion of the dense and lexical result lists.

RRF combines rankings by summing ``weight / (k + rank)`` across lists, so a
chunk ranked highly by either retriever surfaces without needing the two
score scales to be comparable. ``k=60`` is the standard constant; the
dense/lexical weights are configurable.

What RRF cannot see is *why* a list ranked a chunk. A chunk found by one list
scores at most 1/61, and every chunk both lists found at all outscores it, so
the only passage carrying a rare query term — ranked mid-list by ts_rank_cd,
missed by the approximate vector index — falls below the cut. Chunks the
caller names in ``keep`` (the lexical stage's rare-term passages) always
survive it.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from uuid import UUID

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
    keep: Collection[UUID] = (),
) -> list[RetrievedChunk]:
    """Fuse two ranked lists into one, de-duplicated by chunk id.

    The surviving object keeps each retriever's rank and contribution in
    ``components`` (``dense_rank``/``lexical_rank``/``rrf``) for transparency.
    Chunks in ``keep`` survive ``limit``: room is made by dropping the
    lowest-ranked chunks not in ``keep``.
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
            if "rare_term" in chunk.components:
                existing.components["rare_term"] = chunk.components["rare_term"]
            rrf_score[key] += weight / (k + rank)

    contribute(dense, resolved.dense, "dense_rank")
    contribute(lexical, resolved.lexical, "lexical_rank")

    fused = list(merged.values())
    for chunk in fused:
        chunk.with_score("rrf", rrf_score[str(chunk.chunk_id)])
    fused.sort(key=lambda c: c.score, reverse=True)
    if limit is None or len(fused) <= limit:
        return fused
    kept, cut = fused[:limit], fused[limit:]
    rescued = [chunk for chunk in cut if chunk.chunk_id in keep]
    if not rescued:
        return kept
    room = len(rescued)
    trimmed: list[RetrievedChunk] = []
    for chunk in reversed(kept):
        if room and chunk.chunk_id not in keep:
            room -= 1
            continue
        trimmed.append(chunk)
    trimmed.reverse()
    return trimmed + rescued[: limit - len(trimmed)]
