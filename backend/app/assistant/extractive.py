"""The answer when no language model is configured: the sources' own words.

Without a model the assistant cannot write, so it quotes — but quoting a
paper's first sentence gives "We conducted a network meta-analysis…", which
answers nothing. This picks, from each source, the sentence that says the
most about the question: one that carries the question's topic and reads
like a finding or a recommendation, not like a method. One sentence per
paper, strongest sources first, each with its marker, so the reader gets
short, cited bullet points they can follow to the source.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from app.graph.relevance import (
    POPULATION,
    about,
    best_sentence,
    core_words,
    required_hits,
    us_spelling,
    word_relevance,
)
from app.retrieval.types import RetrievedChunk

# The relevance helpers live in app/graph/relevance.py, shared with the
# evidence-search graph; they are re-exported here for the assistant.
__all__ = [
    "POPULATION",
    "about",
    "best_sentence",
    "core_words",
    "extractive_answer",
    "pick_passages",
    "required_hits",
    "us_spelling",
    "word_relevance",
]


class HasSection(Protocol):
    @property
    def section(self) -> str | None: ...


_CONCLUSION = re.compile(r"conclu|interpret|finding|implication", re.I)
_RESULTS = re.compile(r"result", re.I)


def pick_passages[T: HasSection](passages: Sequence[T], limit: int = 2) -> list[T]:
    """The passages of one paper worth reading: its conclusions, then its
    results, then the rest in order — the background and methods an abstract
    opens with say least about what it found."""
    ranked = [
        *[p for p in passages if p.section and _CONCLUSION.search(p.section)],
        *[p for p in passages if p.section and _RESULTS.search(p.section)],
        *passages,
    ]
    chosen: list[T] = []
    for passage in ranked:
        if passage not in chosen:
            chosen.append(passage)
        if len(chosen) == limit:
            break
    return chosen


def extractive_answer(question: str, chunks: Sequence[RetrievedChunk]) -> tuple[str, list[int]]:
    """Bullet points quoted from the sources, and the markers they cite
    (marker n is ``chunks[n - 1]``)."""
    lines: list[str] = []
    used: list[int] = []
    seen_documents: set[object] = set()
    for marker, chunk in enumerate(chunks, start=1):
        key = chunk.pmid or chunk.document_id
        if key in seen_documents:
            continue
        sentence = best_sentence(question, chunk)
        if sentence is None:
            continue
        seen_documents.add(key)
        used.append(marker)
        # The marker inside the sentence, before its full stop: a marker
        # after it is read as the start of the next sentence.
        lines.append(f"- {sentence.rstrip('. ')} [{marker}].")
        if len(lines) == 5:
            break
    return "\n".join(lines), used
