"""Fixed-window chunking — the ablation baseline.

512-token windows with 64-token overlap, packed from whole sentences so a
window never ends mid-sentence. Ignores document structure entirely; that is
the point of a baseline.
"""

from __future__ import annotations

from app.ingestion.models import Chunk, RawDocument
from app.retrieval.chunking.base import estimate_tokens, split_sentences

WINDOW_TOKENS = 512
OVERLAP_TOKENS = 64

_WINDOW_CHARS = WINDOW_TOKENS * 4
_OVERLAP_CHARS = OVERLAP_TOKENS * 4


class FixedWindowChunking:
    name = "fixed"

    async def chunk(self, document: RawDocument) -> list[Chunk]:
        sentences = split_sentences(document.full_text())
        if not sentences:
            return []

        windows: list[list[str]] = []
        current: list[str] = []
        current_chars = 0
        for sentence in sentences:
            added = len(sentence) + (1 if current else 0)
            if current and current_chars + added > _WINDOW_CHARS:
                windows.append(current)
                overlap: list[str] = []
                overlap_chars = 0
                for prior in reversed(current):
                    extra = len(prior) + (1 if overlap else 0)
                    if overlap_chars + extra > _OVERLAP_CHARS:
                        break
                    overlap.insert(0, prior)
                    overlap_chars += extra
                current = overlap
                current_chars = overlap_chars
                added = len(sentence) + (1 if current else 0)
            current.append(sentence)
            current_chars += added
        if current:
            windows.append(current)

        return [
            Chunk(
                chunk_index=index,
                content=" ".join(window),
                token_count=estimate_tokens(" ".join(window)),
                section=None,
                strategy=self.name,
            )
            for index, window in enumerate(windows)
        ]
