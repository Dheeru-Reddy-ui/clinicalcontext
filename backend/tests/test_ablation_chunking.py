"""Ablation scoring logic — exercised in-memory, no DB and no network."""

from __future__ import annotations

from app.ingestion.models import RawDocument, RawSection
from app.retrieval.chunking import get_strategy
from app.retrieval.embedders import HashingEmbedder
from evals.ablation.chunking import render_markdown, run_ablation, score_strategy


def _corpus() -> list[RawDocument]:
    topics = [
        (
            "Apixaban anticoagulation in atrial fibrillation",
            "anticoagulation stroke warfarin apixaban bleeding",
        ),
        (
            "Community acquired pneumonia antibiotic therapy",
            "pneumonia antibiotic amoxicillin respiratory infection",
        ),
        (
            "Metformin glycemic control in type 2 diabetes",
            "diabetes metformin glucose insulin glycemic hemoglobin",
        ),
        (
            "Sertraline for major depressive disorder",
            "depression sertraline ssri antidepressant serotonin mood",
        ),
        (
            "Levothyroxine dosing in hypothyroidism",
            "thyroid levothyroxine tsh hormone hypothyroidism dosing",
        ),
    ]
    docs: list[RawDocument] = []
    for i, (title, terms) in enumerate(topics):
        body = ". ".join(f"{terms} finding {j} was observed in the study" for j in range(4))
        docs.append(
            RawDocument(
                source_type="pubmed",
                title=title,
                pmid=str(1000 + i),
                sections=[
                    RawSection(title="Background", content=f"{terms}. {body}."),
                    RawSection(title="Results", content=f"{body}. {terms} improved outcomes."),
                ],
            )
        )
    return docs


async def test_score_strategy_returns_bounded_metrics() -> None:
    docs = _corpus()
    embedder = HashingEmbedder(dimension=1024)
    strategy = get_strategy("structural")
    chunks_per_doc = [await strategy.chunk(d) for d in docs]

    result = await score_strategy(embedder, docs, chunks_per_doc, strategy_name="structural")

    assert result.documents == len(docs)
    assert result.total_chunks > 0
    assert 0.0 <= result.recall_at_5 <= result.recall_at_10 <= 1.0
    assert 0.0 <= result.mrr_at_10 <= 1.0
    assert result.mean_chunk_tokens > 0


async def test_distinct_topics_are_retrievable() -> None:
    """With five clearly-separated topics, a title should retrieve its own doc."""
    docs = _corpus()
    embedder = HashingEmbedder(dimension=2048)
    chunks_per_doc = [await get_strategy("structural").chunk(d) for d in docs]
    result = await score_strategy(embedder, docs, chunks_per_doc, strategy_name="structural")
    # Topics share almost no vocabulary → retrieval should be near-perfect.
    assert result.recall_at_10 >= 0.8


async def test_run_ablation_covers_all_four_strategies() -> None:
    docs = _corpus()
    report = await run_ablation(docs, embedder_name="local", corpus_documents=len(docs))

    assert {s.strategy for s in report.strategies} == {
        "fixed",
        "recursive",
        "semantic",
        "structural",
    }
    assert report.embedder == "hashing-lexical"
    assert report.sample_size == len(docs)

    markdown = render_markdown(report)
    assert "Recall@10" in markdown
    assert "| structural" in markdown


async def test_empty_strategy_scores_zero_without_crashing() -> None:
    docs = _corpus()
    result = await score_strategy(
        HashingEmbedder(), docs, [[] for _ in docs], strategy_name="fixed"
    )
    assert result.total_chunks == 0
    assert result.recall_at_10 == 0.0
