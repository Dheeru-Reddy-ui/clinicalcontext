"""Semantic chunking — split where the topic shifts.

Embeds each sentence, measures cosine distance between adjacent sentences,
and cuts at the sentences whose distance to their predecessor exceeds a
percentile threshold (a topic boundary). Groups between cuts are further
bounded by a max size so a single semantic run never becomes an enormous
chunk.

The embedder is injected (defaults to the offline :class:`HashingEmbedder`),
so this runs without paid keys; swapping in Cohere makes the boundaries
truly dense-semantic rather than lexical.
"""

from __future__ import annotations

import numpy as np

from app.ingestion.models import Chunk, RawDocument
from app.retrieval.chunking.base import estimate_tokens, split_sentences
from app.retrieval.embedders import HashingEmbedder, TextEmbedder

BREAKPOINT_PERCENTILE = 90.0
MAX_CHUNK_TOKENS = 500


class SemanticChunking:
    name = "semantic"

    def __init__(
        self,
        embedder: TextEmbedder | None = None,
        *,
        breakpoint_percentile: float = BREAKPOINT_PERCENTILE,
        max_chunk_tokens: int = MAX_CHUNK_TOKENS,
    ) -> None:
        self._embedder = embedder if embedder is not None else HashingEmbedder()
        self._percentile = breakpoint_percentile
        self._max_chunk_tokens = max_chunk_tokens

    async def chunk(self, document: RawDocument) -> list[Chunk]:
        sentences = split_sentences(document.full_text())
        if len(sentences) <= 1:
            return _single_chunk(sentences, self.name)

        vectors = np.array(await self._embedder.embed_documents(sentences), dtype=np.float64)
        # Vectors are L2-normalized, so cosine similarity is a dot product;
        # distance = 1 - similarity.
        similarities = np.sum(vectors[:-1] * vectors[1:], axis=1)
        distances = 1.0 - similarities

        threshold = float(np.percentile(distances, self._percentile))
        # A breakpoint before sentence i+1 means "start a new chunk here".
        breakpoints = {int(i + 1) for i, d in enumerate(distances) if d > threshold}

        return self._assemble(sentences, breakpoints)

    def _assemble(self, sentences: list[str], breakpoints: set[int]) -> list[Chunk]:
        chunks: list[Chunk] = []
        current: list[str] = []
        current_chars = 0
        index = 0

        def flush() -> None:
            nonlocal current, current_chars, index
            if not current:
                return
            text = " ".join(current)
            chunks.append(
                Chunk(
                    chunk_index=index,
                    content=text,
                    token_count=estimate_tokens(text),
                    section=None,
                    strategy=self.name,
                )
            )
            index += 1
            current = []
            current_chars = 0

        max_chars = self._max_chunk_tokens * 4
        for i, sentence in enumerate(sentences):
            starts_new = i in breakpoints
            too_big = current and current_chars + len(sentence) > max_chars
            if starts_new or too_big:
                flush()
            current.append(sentence)
            current_chars += len(sentence) + 1
        flush()
        return chunks


def _single_chunk(sentences: list[str], strategy: str) -> list[Chunk]:
    if not sentences:
        return []
    text = sentences[0]
    return [
        Chunk(
            chunk_index=0,
            content=text,
            token_count=estimate_tokens(text),
            section=None,
            strategy=strategy,
        )
    ]
