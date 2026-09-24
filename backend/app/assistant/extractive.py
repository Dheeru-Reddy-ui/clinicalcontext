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

from app.guardrails.grounding import split_sentences
from app.knowledge.terms import search_term
from app.retrieval.types import RetrievedChunk


class HasSection(Protocol):
    @property
    def section(self) -> str | None: ...


# British and American spellings meet ("diarrhoea" finds "diarrhea").
_SPELLINGS = (
    ("oea", "ea"),
    ("haem", "hem"),
    ("paed", "ped"),
    ("oedem", "edem"),
    ("oesoph", "esoph"),
    ("oestr", "estr"),
    ("ischaem", "ischem"),
    ("anaem", "anem"),
    ("leukaem", "leukem"),
    ("tumour", "tumor"),
    ("foet", "fet"),
)
_FINDING = re.compile(
    r"\b(?:conclu\w*|recommend\w*|first[-\s]?line|effective|efficacy|reduc\w*|improv\w*|"
    r"should|superior|non-?inferior|preferred|treated\s+with|treatment\s+of\s+choice|"
    r"dose|dosage|mg\b|associated\s+with|increase\w*|decrease\w*|safe|well[-\s]tolerated|"
    r"indicated|is\s+(?:a|the)\s+(?:common|leading|major))\b",
    re.I,
)
_METHOD = re.compile(
    r"\b(?:we\s+(?:conducted|searched|performed|aimed|included|analy[sz]ed|investigated)|"
    r"this\s+(?:study|review|trial)\s+(?:aims|aimed|was)|were\s+(?:included|searched|enrolled|"
    r"randomi[sz]ed)|databases?|methods?:|objective\w*:|background:|aim\w*:)\b",
    re.I,
)
_LABEL = re.compile(
    r"^(?:background(?:\s+and\s+objectives?)?|objectives?|aims?|purpose|methods?|results?|"
    r"conclusions?|findings|interpretation|importance|context|introduction)\s*[:.\-]?\s+",
    re.I,
)
_WORD = re.compile(r"[a-z0-9]+")


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


def us_spelling(text: str) -> str:
    lowered = text.lower()
    for british, american in _SPELLINGS:
        lowered = lowered.replace(british, american)
    return lowered


def word_relevance(term: str, chunk: RetrievedChunk) -> float:
    """The share of ``term``'s words the passage contains, spelling-blind."""
    words = [us_spelling(w) for w in term.split() if len(w) > 2]
    if not words:
        return 0.0
    haystack = us_spelling(f"{chunk.title or ''} {chunk.content}")
    return sum(1 for w in words if w in haystack) / len(words)


def _topic_words(question: str) -> set[str]:
    return {us_spelling(w) for w in _WORD.findall(search_term(question).lower()) if len(w) > 2}


def best_sentence(question: str, chunk: RetrievedChunk) -> str | None:
    topic = _topic_words(question)
    best: tuple[float, str] | None = None
    for sentence in split_sentences(chunk.content):
        text = _LABEL.sub("", sentence.strip())
        if len(text) < 40 or text.endswith("?"):
            continue
        words = set(_WORD.findall(us_spelling(text)))
        overlap = len(topic & words)
        score = (
            overlap
            + (1.5 if _FINDING.search(text) else 0.0)
            - (2.0 if _METHOD.search(text) else 0.0)
        )
        if overlap == 0 and not _FINDING.search(text):
            continue
        if best is None or score > best[0]:
            best = (score, text)
    return best[1] if best and best[0] > 0 else None


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
