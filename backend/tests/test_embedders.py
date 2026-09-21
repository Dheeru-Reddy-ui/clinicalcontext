"""Local embedder: determinism, normalization, and lexical sensibility."""

from __future__ import annotations

import math

import pytest

from app.retrieval.embedders import HashingEmbedder, get_embedder


async def test_hashing_embedder_is_deterministic() -> None:
    a = HashingEmbedder(dimension=256)
    b = HashingEmbedder(dimension=256)
    [va] = await a.embed_documents(["apixaban reduces stroke in atrial fibrillation"])
    [vb] = await b.embed_documents(["apixaban reduces stroke in atrial fibrillation"])
    assert va == vb


async def test_vectors_are_unit_norm() -> None:
    embedder = HashingEmbedder(dimension=512)
    vectors = await embedder.embed_documents(["hello world", "clinical evidence grade"])
    for vector in vectors:
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)


async def test_empty_text_is_zero_vector() -> None:
    [vector] = await HashingEmbedder(dimension=64).embed_documents([""])
    assert all(v == 0.0 for v in vector)


async def test_shared_vocabulary_scores_higher_than_disjoint() -> None:
    embedder = HashingEmbedder(dimension=2048)

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    query, related, unrelated = await embedder.embed_documents(
        [
            "anticoagulation therapy for atrial fibrillation",
            "atrial fibrillation anticoagulation reduces stroke",
            "photosynthesis in green plants and chloroplasts",
        ]
    )
    assert cosine(query, related) > cosine(query, unrelated)


async def test_get_embedder_resolves_names() -> None:
    assert isinstance(get_embedder("local"), HashingEmbedder)
    assert isinstance(get_embedder("hashing"), HashingEmbedder)
    with pytest.raises(ValueError, match="unknown embedder"):
        get_embedder("mystery-model")
