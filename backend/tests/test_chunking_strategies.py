"""The four chunking strategies: protocol conformance and per-strategy behavior."""

from __future__ import annotations

import pytest

from app.ingestion.models import RawDocument, RawSection
from app.retrieval.chunking import (
    STRATEGY_NAMES,
    ChunkingStrategy,
    FixedWindowChunking,
    RecursiveChunking,
    SemanticChunking,
    StructuralChunking,
    get_strategy,
)
from app.retrieval.chunking.fixed import OVERLAP_TOKENS, WINDOW_TOKENS
from app.retrieval.embedders import HashingEmbedder


def _paper() -> RawDocument:
    return RawDocument(
        source_type="pubmed",
        title="Apixaban versus warfarin in atrial fibrillation with CKD",
        pmid="12345678",
        abstract="full",
        sections=[
            RawSection(
                title="Background",
                content=(
                    "Anticoagulation in advanced chronic kidney disease is uncertain. "
                    "Warfarin has been the historical standard of care. "
                    "Direct oral anticoagulants are increasingly used."
                ),
            ),
            RawSection(
                title="Methods",
                content=(
                    "We randomized 1200 patients with atrial fibrillation and stage 4 CKD. "
                    "The primary outcome was stroke or systemic embolism. "
                    "Bleeding was a key safety endpoint."
                ),
            ),
            RawSection(
                title="Results",
                content=(
                    "Apixaban reduced stroke compared with warfarin. "
                    "Major bleeding was less frequent with apixaban. "
                    "Mortality did not differ significantly."
                ),
            ),
        ],
    )


@pytest.mark.parametrize("name", STRATEGY_NAMES)
async def test_every_strategy_conforms_and_produces_chunks(name: str) -> None:
    strategy = get_strategy(name)
    assert isinstance(strategy, ChunkingStrategy)
    assert strategy.name == name

    chunks = await strategy.chunk(_paper())
    assert chunks, f"{name} produced no chunks"
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(c.strategy == name for c in chunks)
    assert all(c.content.strip() for c in chunks)
    assert all(c.token_count > 0 for c in chunks)


async def test_get_strategy_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown chunking strategy"):
        get_strategy("nonexistent")


async def test_structural_prepends_title_and_section_to_embed_text_only() -> None:
    chunks = await StructuralChunking().chunk(_paper())
    for chunk in chunks:
        # The stored content is the clean passage...
        assert "Apixaban versus warfarin" not in chunk.content or chunk.section is not None
        # ...but the embedded text carries title + section context.
        assert chunk.embed_text is not None
        assert "Apixaban versus warfarin in atrial fibrillation with CKD" in chunk.embed_text
        assert chunk.embedding_input == chunk.embed_text
        if chunk.section:
            assert chunk.section in chunk.embed_text


async def test_structural_keeps_sections_separate() -> None:
    chunks = await StructuralChunking().chunk(_paper())
    sections_seen = {c.section for c in chunks}
    assert {"Background", "Methods", "Results"} <= sections_seen
    for chunk in chunks:
        # Content from one section never bleeds into another.
        assert not ("randomized 1200" in chunk.content and "reduced stroke" in chunk.content)


async def test_fixed_and_recursive_ignore_structure() -> None:
    fixed = await FixedWindowChunking().chunk(_paper())
    recursive = await RecursiveChunking().chunk(_paper())
    # Flat strategies carry no section labels and embed content verbatim.
    assert all(c.section is None for c in fixed)
    assert all(c.section is None for c in recursive)
    assert all(c.embed_text is None for c in fixed)


async def test_fixed_windows_respect_size_bound() -> None:
    long_section = RawSection(
        title=None,
        content=" ".join(f"Sentence number {i} about anticoagulation therapy." for i in range(200)),
    )
    document = RawDocument(source_type="pubmed", title="Long", sections=[long_section])
    chunks = await FixedWindowChunking().chunk(document)
    assert len(chunks) > 1
    one_sentence = 8  # ~tokens
    for chunk in chunks:
        assert chunk.token_count <= WINDOW_TOKENS + one_sentence
    assert OVERLAP_TOKENS < WINDOW_TOKENS  # sanity on the constants


async def test_semantic_splits_on_topic_shift() -> None:
    # Two topics, each internally vocabulary-cohesive so a lexical embedder
    # sees high within-topic similarity and a sharp cross-topic distance spike.
    document = RawDocument(
        source_type="pubmed",
        title="Mixed",
        sections=[
            RawSection(
                title=None,
                content=(
                    "Atrial fibrillation anticoagulation reduces stroke risk. "
                    "Anticoagulation for atrial fibrillation lowers stroke incidence. "
                    "Stroke prevention in atrial fibrillation relies on anticoagulation. "
                    "Photosynthesis in chloroplasts converts sunlight to energy. "
                    "Chloroplast photosynthesis produces oxygen from sunlight. "
                    "Sunlight drives photosynthesis within plant chloroplasts."
                ),
            )
        ],
    )
    chunks = await SemanticChunking(embedder=HashingEmbedder()).chunk(document)
    assert len(chunks) >= 2, "a sharp topic boundary should force a split"
    # The anticoagulation topic and the photosynthesis topic land in different chunks.
    joined = [c.content for c in chunks]
    assert not any("anticoagulation" in c and "photosynthesis" in c for c in joined)


async def test_semantic_handles_single_sentence() -> None:
    document = RawDocument(
        source_type="pubmed",
        title="Tiny",
        sections=[RawSection(title=None, content="A single sentence document.")],
    )
    chunks = await SemanticChunking().chunk(document)
    assert len(chunks) == 1


async def test_empty_document_yields_no_chunks() -> None:
    empty = RawDocument(source_type="pubmed", title="Empty", sections=[])
    for name in STRATEGY_NAMES:
        assert await get_strategy(name).chunk(empty) == []
