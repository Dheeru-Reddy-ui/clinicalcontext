"""Pluggable text embedders behind one interface.

Two implementations share the :class:`TextEmbedder` protocol:

- :class:`HashingEmbedder` — a deterministic, offline lexical embedder (the
  hashing trick + sublinear TF, L2-normalized). No network, no API key,
  reproducible. Used by semantic chunking and the ablation so both run
  without paid keys; the numbers it produces are genuinely measured, just
  lexical rather than dense-semantic.
- :class:`CohereTextEmbedder` — Cohere embed-v4.0, the production embedder.

Holding the embedder constant across chunking strategies makes the ablation a
fair comparison whichever one is selected.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Protocol, runtime_checkable

_TOKEN = re.compile(r"[a-z0-9]+")


@runtime_checkable
class TextEmbedder(Protocol):
    name: str
    dimension: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_queries(self, texts: list[str]) -> list[list[float]]: ...


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class HashingEmbedder:
    """Deterministic lexical embedder (hashing trick + sublinear TF, L2-norm).

    Cross-process stable: token buckets come from a stable digest, not Python's
    salted ``hash``. Query and document embeddings are identical (lexical, no
    asymmetric input types).
    """

    name = "hashing-lexical"

    def __init__(self, dimension: int = 1024) -> None:
        self.dimension = dimension

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        counts = Counter(_tokenize(text))
        if not counts:
            return vector
        for token, count in counts.items():
            bucket = (
                int.from_bytes(
                    hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest(), "big"
                )
                % self.dimension
            )
            # Sublinear term frequency dampens repeated-term dominance.
            vector[bucket] += 1.0 + math.log(count)
        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        return vector

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    async def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]


class CohereTextEmbedder:
    """Cohere embed-v4.0 adapter (the production embedder)."""

    name = "cohere-embed-v4.0"

    def __init__(self, *, dimension: int = 1536) -> None:
        from cohere import AsyncClientV2

        from app.config import get_settings

        self.dimension = dimension
        self._model = "embed-v4.0"
        self._client = AsyncClientV2(api_key=get_settings().cohere_api_key.get_secret_value())

    async def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        from app.core.telemetry import span

        with span(
            "provider.cohere.embed", model=self._model, texts=len(texts), input_type=input_type
        ):
            return await self._embed_call(texts, input_type)

    async def _embed_call(self, texts: list[str], input_type: str) -> list[list[float]]:
        response = await self._client.embed(
            model=self._model,
            texts=texts,
            input_type=input_type,
            embedding_types=["float"],
            output_dimension=self.dimension,
        )
        vectors = response.embeddings.float_
        if vectors is None or len(vectors) != len(texts):
            raise RuntimeError("Cohere returned an unexpected embeddings payload")
        return [list(vector) for vector in vectors]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, "search_document")

    async def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, "search_query")


# The production corpus column is vector(1536); the local embedder matches it
# so it is a true drop-in for Cohere in the DB, dense search, and backfill.
LOCAL_EMBED_DIMENSION = 1536


def get_embedder(name: str) -> TextEmbedder:
    """Resolve an embedder by name for the ablation / config."""
    if name in ("local", "hashing", "hashing-lexical"):
        return HashingEmbedder(dimension=LOCAL_EMBED_DIMENSION)
    if name in ("cohere", "cohere-embed-v4.0"):
        return CohereTextEmbedder()
    raise ValueError(f"unknown embedder: {name!r}")
