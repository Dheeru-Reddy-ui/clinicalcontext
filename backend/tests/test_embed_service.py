"""EmbeddingService: caching, input-type separation, batching (fake deps)."""

from __future__ import annotations

import json
from typing import Any

from app.retrieval.embed import EmbeddingService


class FakeEmbedder:
    """Counts calls and returns a distinguishable vector per input type."""

    name = "fake"
    dimension = 4

    def __init__(self) -> None:
        self.document_calls = 0
        self.query_calls = 0
        self.embedded_texts: list[str] = []

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls += 1
        self.embedded_texts.extend(texts)
        return [[1.0, 0.0, 0.0, float(len(t))] for t in texts]

    async def embed_queries(self, texts: list[str]) -> list[list[float]]:
        self.query_calls += 1
        return [[0.0, 1.0, 0.0, float(len(t))] for t in texts]


class FakeRedis:
    """Just enough of the async redis surface for the cache path."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.store.get(k) for k in keys]

    def pipeline(self) -> FakeRedis:
        self._buffer: list[tuple[str, str]] = []
        return self

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._buffer.append((key, value))

    async def execute(self) -> None:
        for key, value in self._buffer:
            self.store[key] = value
        self._buffer = []


async def test_document_and_query_use_different_input_types() -> None:
    embedder = FakeEmbedder()
    service = EmbeddingService(embedder)

    [doc_vector] = await service.embed_texts(["hello"], "search_document")
    query_vector = await service.embed_query("hello")

    assert embedder.document_calls == 1
    assert embedder.query_calls == 1
    # Different input types must not collide (distinguishable vectors here).
    assert doc_vector != query_vector


async def test_cache_hit_skips_the_embedder() -> None:
    embedder = FakeEmbedder()
    redis = FakeRedis()
    service = EmbeddingService(embedder, redis)  # type: ignore[arg-type]

    first = await service.embed_texts(["apixaban"], "search_document")
    second = await service.embed_texts(["apixaban"], "search_document")

    assert first == second
    assert embedder.document_calls == 1  # second call served from cache


async def test_cache_key_separates_input_types() -> None:
    embedder = FakeEmbedder()
    redis = FakeRedis()
    service = EmbeddingService(embedder, redis)  # type: ignore[arg-type]

    await service.embed_texts(["stroke"], "search_document")
    await service.embed_texts(["stroke"], "search_query")

    # Same text, different input types → two distinct cache entries, two calls.
    assert embedder.document_calls == 1
    assert embedder.query_calls == 1
    assert len(redis.store) == 2


async def test_only_cache_misses_hit_the_embedder() -> None:
    embedder = FakeEmbedder()
    redis = FakeRedis()
    service = EmbeddingService(embedder, redis)  # type: ignore[arg-type]

    await service.embed_texts(["a"], "search_document")
    embedder.embedded_texts.clear()
    await service.embed_texts(["a", "b", "c"], "search_document")

    # "a" was cached; only "b" and "c" are freshly embedded.
    assert set(embedder.embedded_texts) == {"b", "c"}


async def test_batching_splits_large_inputs() -> None:
    embedder = FakeEmbedder()
    service = EmbeddingService(embedder, batch_size=2)

    texts = ["t1", "t2", "t3", "t4", "t5"]
    vectors = await service.embed_texts(texts, "search_document")

    assert len(vectors) == 5
    assert embedder.document_calls == 3  # ceil(5 / 2)


async def test_cached_vector_roundtrips_through_json() -> None:
    redis = FakeRedis()
    service = EmbeddingService(FakeEmbedder(), redis)  # type: ignore[arg-type]
    await service.embed_texts(["x"], "search_document")
    stored: Any = next(iter(redis.store.values()))
    assert isinstance(json.loads(stored), list)
