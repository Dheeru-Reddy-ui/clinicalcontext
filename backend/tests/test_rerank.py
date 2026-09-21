"""BM25 reranker: reorders by query↔passage relevance, honors top_n."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from app.retrieval.rerank import BM25Reranker, get_reranker
from app.retrieval.types import RetrievedChunk


def _chunk(content: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        content=content,
        section=None,
        title="t",
        publication_date=date(2020, 1, 1),
        evidence_grade="B",
        study_type=None,
        score=0.0,
    )


async def test_reranker_puts_the_on_topic_passage_first() -> None:
    chunks = [
        _chunk("Photosynthesis occurs in the chloroplasts of green plants."),
        _chunk("Apixaban reduces stroke risk in atrial fibrillation with renal impairment."),
        _chunk("The weather forecast predicts rain over the coastal region tomorrow."),
    ]
    reranked = await BM25Reranker().rerank(
        "apixaban anticoagulation atrial fibrillation", chunks, top_n=3
    )
    assert "Apixaban" in reranked[0].content
    assert reranked[0].components["rerank"] > reranked[1].components["rerank"]


async def test_reranker_respects_top_n() -> None:
    chunks = [_chunk(f"passage {i} about anticoagulation therapy") for i in range(20)]
    reranked = await BM25Reranker().rerank("anticoagulation", chunks, top_n=8)
    assert len(reranked) == 8


async def test_reranker_handles_empty() -> None:
    assert await BM25Reranker().rerank("anything", [], top_n=8) == []


async def test_reranker_scores_are_recorded() -> None:
    chunks = [_chunk("metformin controls blood glucose in type 2 diabetes")]
    [chunk] = await BM25Reranker().rerank("metformin diabetes glucose", chunks, top_n=1)
    assert chunk.components["rerank"] > 0
    assert chunk.score == chunk.components["rerank"]


def test_get_reranker_resolves_names() -> None:
    assert isinstance(get_reranker("local"), BM25Reranker)
    assert isinstance(get_reranker("bm25"), BM25Reranker)
    import pytest

    with pytest.raises(ValueError, match="unknown reranker"):
        get_reranker("mystery")
