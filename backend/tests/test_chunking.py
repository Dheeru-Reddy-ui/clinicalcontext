"""Chunking: section isolation, sentence packing, overlap, index ordering."""

from __future__ import annotations

from itertools import pairwise

from app.ingestion.models import RawSection
from app.retrieval.chunking import (
    OVERLAP_TOKENS,
    TARGET_TOKENS,
    chunk_sections,
    estimate_tokens,
)


def test_empty_sections_produce_no_chunks() -> None:
    assert chunk_sections([]) == []
    assert chunk_sections([RawSection(title="Methods", content="   ")]) == []


def test_short_sections_become_single_chunks_with_section_labels() -> None:
    sections = [
        RawSection(title="Background", content="CKD complicates anticoagulation."),
        RawSection(title="Results", content="Apixaban reduced stroke risk substantially."),
    ]
    chunks = chunk_sections(sections)

    assert [c.section for c in chunks] == ["Background", "Results"]
    assert [c.chunk_index for c in chunks] == [0, 1]
    assert all(c.token_count == estimate_tokens(c.content) for c in chunks)


def test_sections_never_blend() -> None:
    sections = [
        RawSection(title="Methods", content="We enrolled patients. " * 40),
        RawSection(title="Results", content="Mortality fell. " * 40),
    ]
    chunks = chunk_sections(sections)
    for chunk in chunks:
        assert not ("enrolled" in chunk.content and "Mortality" in chunk.content), (
            "Methods and Results text must not share a chunk"
        )


def test_long_section_splits_with_overlap_and_bounded_size() -> None:
    sentences = [f"Finding number {i} was observed in the cohort." for i in range(120)]
    sections = [RawSection(title="Results", content=" ".join(sentences))]

    chunks = chunk_sections(sections)

    assert len(chunks) > 1
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        # A chunk may exceed the target by at most one sentence.
        assert chunk.token_count <= TARGET_TOKENS + estimate_tokens(sentences[0])

    # Overlap: the first sentence of chunk N+1 already appeared in chunk N.
    for first, second in pairwise(chunks):
        lead_sentence = second.content.split(".")[0]
        assert lead_sentence in first.content

    # Overlap is bounded — chunks are not mostly repetition.
    for first, second in pairwise(chunks):
        shared = set(first.content.split(". ")) & set(second.content.split(". "))
        shared_tokens = estimate_tokens(". ".join(shared))
        assert shared_tokens <= OVERLAP_TOKENS + estimate_tokens(sentences[0])
