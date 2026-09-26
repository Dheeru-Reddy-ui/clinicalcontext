"""Reciprocal Rank Fusion: math, weighting, de-duplication, transparency."""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from app.retrieval.fusion import RRF_K, FusionWeights, reciprocal_rank_fusion
from app.retrieval.types import RetrievedChunk


def _chunk(chunk_id: UUID, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=uuid4(),
        content="passage",
        section=None,
        title="t",
        publication_date=date(2020, 1, 1),
        evidence_grade="B",
        study_type=None,
        score=score,
    )


def test_rrf_rewards_agreement_between_retrievers() -> None:
    shared = uuid4()
    dense = [_chunk(shared, 0.9), _chunk(uuid4(), 0.8)]
    lexical = [_chunk(shared, 5.0), _chunk(uuid4(), 4.0)]

    fused = reciprocal_rank_fusion(dense, lexical)

    # The chunk ranked #1 by both retrievers must fuse to the top.
    assert fused[0].chunk_id == shared
    expected = 1.0 / (RRF_K + 1) + 1.0 / (RRF_K + 1)
    assert abs(fused[0].components["rrf"] - expected) < 1e-9


def test_rrf_deduplicates_and_keeps_both_ranks() -> None:
    shared = uuid4()
    fused = reciprocal_rank_fusion([_chunk(shared, 0.9)], [_chunk(shared, 3.0)])
    assert len(fused) == 1
    chunk = fused[0]
    assert chunk.components["dense_rank"] == 1.0
    assert chunk.components["lexical_rank"] == 1.0


def test_rrf_union_of_both_lists() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    fused = reciprocal_rank_fusion([_chunk(a, 1.0), _chunk(b, 0.5)], [_chunk(c, 2.0)])
    assert {chunk.chunk_id for chunk in fused} == {a, b, c}


def test_weighting_favors_the_heavier_retriever() -> None:
    dense_only = uuid4()
    lexical_only = uuid4()
    dense = [_chunk(dense_only, 0.9)]
    lexical = [_chunk(lexical_only, 5.0)]

    lexical_heavy = reciprocal_rank_fusion(
        dense, lexical, weights=FusionWeights(dense=0.1, lexical=1.0)
    )
    assert lexical_heavy[0].chunk_id == lexical_only


def test_limit_truncates() -> None:
    dense = [_chunk(uuid4(), 1.0) for _ in range(10)]
    fused = reciprocal_rank_fusion(dense, [], limit=3)
    assert len(fused) == 3


def test_empty_inputs_produce_empty_output() -> None:
    assert reciprocal_rank_fusion([], []) == []


def test_a_kept_chunk_survives_the_cut() -> None:
    """Found by one list at rank 7, a chunk scores 1/67 and falls below five
    chunks both lists found; named in ``keep``, it takes the place of the
    lowest-ranked of them instead."""
    both = [uuid4() for _ in range(6)]
    rare = uuid4()
    dense = [_chunk(i, 1.0) for i in both]
    lexical = [_chunk(i, 1.0) for i in both] + [_chunk(rare, 0.1)]

    assert rare not in {c.chunk_id for c in reciprocal_rank_fusion(dense, lexical, limit=5)}
    fused = reciprocal_rank_fusion(dense, lexical, limit=5, keep={rare})
    assert [c.chunk_id for c in fused] == [*both[:4], rare]


def test_keep_never_displaces_another_kept_chunk() -> None:
    top = [uuid4() for _ in range(3)]
    kept_low, rescued = uuid4(), uuid4()
    dense = [_chunk(i, 1.0) for i in [*top, kept_low]]
    lexical = [_chunk(i, 1.0) for i in [*top, kept_low]] + [_chunk(rescued, 0.1)]
    fused = reciprocal_rank_fusion(dense, lexical, limit=4, keep={kept_low, rescued})
    assert [c.chunk_id for c in fused] == [*top[:2], kept_low, rescued]


def test_the_rare_term_flag_survives_fusion() -> None:
    shared = uuid4()
    flagged = _chunk(shared, 3.0)
    flagged.components["rare_term"] = 1.0
    [fused] = reciprocal_rank_fusion([_chunk(shared, 0.9)], [flagged])
    assert fused.components["rare_term"] == 1.0
