"""Structural chunking — split on the document's own section structure.

Sections never blend (a Methods sentence must not land in a Results chunk —
section identity matters for evidence assessment). Within a section, whole
sentences are packed toward a target size with sentence-level overlap.

The defining feature: each chunk's *embedded* text is prefixed with the
document title and section header, so no chunk is ever context-free, while the
stored `content` (what a citation shows) stays the clean passage. Points #4 of
the phase brief.
"""

from __future__ import annotations

from app.ingestion.models import Chunk, RawDocument, RawSection
from app.retrieval.chunking.base import estimate_tokens, normalize_ws, split_sentences

TARGET_TOKENS = 450
OVERLAP_TOKENS = 60
MIN_CHUNK_TOKENS = 20

_TARGET_CHARS = TARGET_TOKENS * 4
_OVERLAP_CHARS = OVERLAP_TOKENS * 4


def _pack_section(sentences: list[str]) -> list[str]:
    """Pack sentences into ~TARGET_TOKENS windows with sentence overlap.

    Character accounting on the joined window keeps a finished chunk's token
    estimate within one sentence of the target.
    """
    windows: list[str] = []
    current: list[str] = []
    current_chars = 0
    for sentence in sentences:
        added = len(sentence) + (1 if current else 0)
        if current and current_chars + added > _TARGET_CHARS:
            windows.append(" ".join(current))
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
        windows.append(" ".join(current))
    return windows


def chunk_sections(sections: list[RawSection]) -> list[Chunk]:
    """Section-aware, sentence-preserving chunking (no context prefix).

    Retained as the ingestion pipeline's chunker and the boundary logic the
    structural strategy builds on.
    """
    chunks: list[Chunk] = []
    index = 0
    for section in sections:
        sentences = split_sentences(section.content)
        if not sentences:
            continue
        section_title = normalize_ws(section.title) if section.title else None
        for window in _pack_section(sentences):
            tokens = estimate_tokens(window)
            if tokens < MIN_CHUNK_TOKENS and chunks and chunks[-1].section == section_title:
                merged = f"{chunks[-1].content} {window}"
                chunks[-1] = Chunk(
                    chunk_index=chunks[-1].chunk_index,
                    content=merged,
                    token_count=estimate_tokens(merged),
                    section=chunks[-1].section,
                )
                continue
            chunks.append(
                Chunk(
                    chunk_index=index,
                    content=window,
                    token_count=tokens,
                    section=section_title,
                )
            )
            index += 1
    return chunks


def _context_prefix(title: str, section: str | None) -> str:
    header = f"{title}" if title else ""
    if section:
        header = f"{header}\n{section}" if header else section
    return header


class StructuralChunking:
    name = "structural"

    async def chunk(self, document: RawDocument) -> list[Chunk]:
        base = chunk_sections(document.sections)
        enriched: list[Chunk] = []
        for chunk in base:
            prefix = _context_prefix(document.title, chunk.section)
            embed_text = f"{prefix}\n\n{chunk.content}" if prefix else chunk.content
            enriched.append(
                Chunk(
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    token_count=chunk.token_count,
                    section=chunk.section,
                    strategy=self.name,
                    embed_text=embed_text,
                )
            )
        return enriched
