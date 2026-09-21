"""Chunking foundations: the strategy protocol and shared text utilities.

All four strategies implement :class:`ChunkingStrategy` and are therefore
interchangeable and config-selectable. ``chunk`` is async so the semantic
strategy can await sentence embeddings; the CPU-only strategies just don't
await anything.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from app.ingestion.models import Chunk, RawDocument

# Token accounting is a deterministic chars/4 estimate — enough for packing
# and stats; provider-exact counts arrive with the embedding call.
CHARS_PER_TOKEN = 4

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[\"'])")
_PARAGRAPH_BOUNDARY = re.compile(r"\n\s*\n+")
_WHITESPACE = re.compile(r"\s+")


def estimate_tokens(text: str) -> int:
    """~4 chars/token for English biomedical prose."""
    return max(1, round(len(text) / CHARS_PER_TOKEN))


def normalize_ws(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def split_sentences(text: str) -> list[str]:
    normalized = normalize_ws(text)
    if not normalized:
        return []
    return [s for s in _SENTENCE_BOUNDARY.split(normalized) if s.strip()]


def split_paragraphs(text: str) -> list[str]:
    return [p for p in (_PARAGRAPH_BOUNDARY.split(text)) if p.strip()]


@runtime_checkable
class ChunkingStrategy(Protocol):
    """A named, interchangeable way to break a document into chunks."""

    name: str

    async def chunk(self, document: RawDocument) -> list[Chunk]: ...
