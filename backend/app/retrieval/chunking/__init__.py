"""Chunking strategies: fixed, recursive, semantic, structural.

All four implement :class:`ChunkingStrategy` and are selectable by name via
:func:`get_strategy`. The ingestion pipeline uses :func:`chunk_sections`
(structural boundary logic) directly.
"""

from __future__ import annotations

from app.retrieval.chunking.base import (
    ChunkingStrategy,
    estimate_tokens,
    normalize_ws,
    split_paragraphs,
    split_sentences,
)
from app.retrieval.chunking.fixed import FixedWindowChunking
from app.retrieval.chunking.recursive import RecursiveChunking
from app.retrieval.chunking.semantic import SemanticChunking
from app.retrieval.chunking.structural import (
    OVERLAP_TOKENS,
    TARGET_TOKENS,
    StructuralChunking,
    chunk_sections,
)

# Names double as the value stored in chunks.strategy.
STRATEGY_NAMES = ("fixed", "recursive", "semantic", "structural")


def get_strategy(name: str) -> ChunkingStrategy:
    """Resolve a chunking strategy by name (config-driven selection)."""
    match name:
        case "fixed":
            return FixedWindowChunking()
        case "recursive":
            return RecursiveChunking()
        case "semantic":
            return SemanticChunking()
        case "structural":
            return StructuralChunking()
        case _:
            raise ValueError(f"unknown chunking strategy: {name!r}")


__all__ = [
    "OVERLAP_TOKENS",
    "STRATEGY_NAMES",
    "TARGET_TOKENS",
    "ChunkingStrategy",
    "FixedWindowChunking",
    "RecursiveChunking",
    "SemanticChunking",
    "StructuralChunking",
    "chunk_sections",
    "estimate_tokens",
    "get_strategy",
    "normalize_ws",
    "split_paragraphs",
    "split_sentences",
]
