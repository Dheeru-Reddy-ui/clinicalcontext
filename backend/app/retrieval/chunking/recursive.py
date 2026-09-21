"""Recursive character chunking.

Descends a separator hierarchy — paragraphs, then sentences — packing whole
units toward a target size and only cutting finer when a single unit already
exceeds the target. Respects natural boundaries without needing labeled
document structure.
"""

from __future__ import annotations

from app.ingestion.models import Chunk, RawDocument
from app.retrieval.chunking.base import (
    estimate_tokens,
    normalize_ws,
    split_paragraphs,
    split_sentences,
)

TARGET_TOKENS = 400
_TARGET_CHARS = TARGET_TOKENS * 4


class RecursiveChunking:
    name = "recursive"

    async def chunk(self, document: RawDocument) -> list[Chunk]:
        text = document.full_text()
        units = self._units(text)
        if not units:
            return []

        chunks: list[str] = []
        current = ""
        for unit in units:
            candidate = f"{current} {unit}".strip() if current else unit
            if current and len(candidate) > _TARGET_CHARS:
                chunks.append(current)
                current = unit
            else:
                current = candidate
        if current:
            chunks.append(current)

        return [
            Chunk(
                chunk_index=index,
                content=text_chunk,
                token_count=estimate_tokens(text_chunk),
                section=None,
                strategy=self.name,
            )
            for index, text_chunk in enumerate(chunks)
        ]

    def _units(self, text: str) -> list[str]:
        """Paragraphs, breaking any over-long paragraph into sentences."""
        units: list[str] = []
        for paragraph in split_paragraphs(text) or [text]:
            normalized = normalize_ws(paragraph)
            if not normalized:
                continue
            if len(normalized) <= _TARGET_CHARS:
                units.append(normalized)
            else:
                units.extend(split_sentences(normalized))
        return units
