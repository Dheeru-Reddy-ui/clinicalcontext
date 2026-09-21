"""The corpus-derived medical vocabulary behind boosting and correction (11B.2).

The boost list is built from what the corpus is actually indexed under — the
MeSH vocabulary the ingestion phase extracted (``public.mesh_terms``, ranked
by document count) — plus every name on the LASA confusion table (11B.3), so
the recognizer is biased toward exactly the drugs the safety gate watches.
Nothing here is a hand-typed list of "important drugs": if the corpus
changes, the vocabulary changes with it (``refresh_if_stale``).

The same vocabulary indexes the post-STT correction pass (11B.4).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from app.voice.lasa import LasaTable

if TYPE_CHECKING:
    import asyncpg

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("app.voice.vocabulary")

# Suffixes that mark INN drug stems — used to *rank* drug names first in the
# boost list and to decide which transcript tokens the LASA gate looks at.
_DRUG_SUFFIXES = (
    "mab", "nib", "pril", "sartan", "olol", "statin", "azole", "cillin", "mycin",
    "oxacin", "dipine", "parin", "xaban", "gliptin", "flozin", "glutide", "tide",
    "prazole", "tidine", "vir", "zepam", "zolam", "triptan", "profen", "oxetine",
    "afil", "asone", "onide", "lukast", "terol", "tropium", "semide", "thiazide",
    "lactone", "grel", "grelor", "ximab", "zumab", "lizumab", "tinib", "ciclib",
    "rafenib", "mustine", "platin", "rubicin", "taxel", "bicin", "vudine", "navir",
    "buvir", "gatran", "iximab", "azepine", "gabalin", "pentin", "iracetam",
    "idone", "apine", "zine", "phrine", "adol", "orphine", "codone", "morphone",
    "fentanil", "caine", "curium", "curonium", "setron", "peridol", "formin",
    "glinide", "gliflozin",
)  # fmt: skip

_TOKEN = re.compile(r"[a-z][a-z0-9'-]{2,}")


def looks_like_drug(term: str) -> bool:
    lowered = term.lower().strip()
    if not lowered.isalpha() and "-" not in lowered:
        return False
    return any(lowered.endswith(suffix) for suffix in _DRUG_SUFFIXES)


@dataclass(slots=True)
class VoiceVocabulary:
    """A snapshot of the vocabulary. Immutable after build; rebuilt on refresh."""

    terms: list[str]  # ranked boost terms (corpus drugs by frequency, LASA, the rest)
    drug_terms: frozenset[str]
    all_terms: frozenset[str]
    corpus_signature: str  # what the snapshot was built from
    built_at: float = field(default_factory=time.monotonic)

    def boost_terms(self, limit: int) -> list[str]:
        return self.terms[:limit]

    def is_known(self, token: str) -> bool:
        return token.lower() in self.all_terms


def build_vocabulary(
    mesh_rows: list[tuple[str, int]],
    lasa: LasaTable,
    *,
    corpus_signature: str,
    max_terms: int = 2000,
) -> VoiceVocabulary:
    """Rank: corpus drug-like terms by document count, then the ISMP LASA
    names, then the other corpus terms by document count.

    The head of the list is what a recognizer with a small boost budget gets
    (whisper's prompt: ~15 terms), so it must be the names *this corpus*
    asks about — a recognizer biased toward LASA names the corpus never
    mentions hears nothing better. The LASA names still rank above the
    common conditions: they are the rare words, and the full list feeds the
    correction pass and a cloud recognizer's larger keyterm budget.
    """
    seen: set[str] = set()
    ranked: list[str] = []

    def add(term: str) -> None:
        cleaned = term.strip().lower()
        if len(cleaned) < 4 or cleaned in seen:
            return
        seen.add(cleaned)
        ranked.append(cleaned)

    drugs: set[str] = set()
    corpus_sorted = sorted(mesh_rows, key=lambda row: -row[1])
    for term, _count in corpus_sorted:
        if looks_like_drug(term):
            add(term)
            drugs.add(term.lower())
    for name in lasa.names():
        add(name)
        drugs.add(name)
    for term, _count in corpus_sorted:
        add(term)
    ranked = ranked[:max_terms]
    return VoiceVocabulary(
        terms=ranked,
        drug_terms=frozenset(drugs),
        all_terms=frozenset(ranked),
        corpus_signature=corpus_signature,
    )


async def load_vocabulary(pool: DbPool, lasa: LasaTable) -> VoiceVocabulary:
    """Read the corpus vocabulary (service context: shared reference data)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT term, document_count FROM public.mesh_terms "
            "ORDER BY document_count DESC LIMIT 5000"
        )
        signature = await conn.fetchval(
            "SELECT count(*)::text || ':' || coalesce(max(document_count), 0)::text "
            "FROM public.mesh_terms"
        )
    mesh_rows = [(str(r["term"]), int(r["document_count"])) for r in rows]
    vocabulary = build_vocabulary(mesh_rows, lasa, corpus_signature=str(signature))
    logger.info(
        "voice_vocabulary_built",
        terms=len(vocabulary.terms),
        drug_terms=len(vocabulary.drug_terms),
        corpus_signature=vocabulary.corpus_signature,
    )
    return vocabulary


class VocabularyCache:
    """Process-wide vocabulary with corpus-change detection.

    ``get`` returns the snapshot; every ``ttl_seconds`` it re-reads the cheap
    signature query and rebuilds only when the corpus vocabulary changed —
    "refresh it when the corpus updates" without a scheduler dependency.
    """

    def __init__(self, lasa: LasaTable, *, ttl_seconds: float = 300.0) -> None:
        self._lasa = lasa
        self._ttl = ttl_seconds
        self._snapshot: VoiceVocabulary | None = None
        self._checked_at = 0.0

    async def get(self, pool: DbPool | None) -> VoiceVocabulary:
        if pool is None:
            if self._snapshot is None:
                self._snapshot = build_vocabulary([], self._lasa, corpus_signature="no-db")
            return self._snapshot
        now = time.monotonic()
        if self._snapshot is None:
            self._snapshot = await load_vocabulary(pool, self._lasa)
            self._checked_at = now
        elif now - self._checked_at > self._ttl:
            self._checked_at = now
            async with pool.acquire() as conn:
                signature = await conn.fetchval(
                    "SELECT count(*)::text || ':' || coalesce(max(document_count), 0)::text "
                    "FROM public.mesh_terms"
                )
            if str(signature) != self._snapshot.corpus_signature:
                self._snapshot = await load_vocabulary(pool, self._lasa)
        return self._snapshot

    def set_for_tests(self, snapshot: VoiceVocabulary) -> None:
        self._snapshot = snapshot
        self._checked_at = time.monotonic()


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def describe(vocabulary: VoiceVocabulary) -> dict[str, Any]:
    return {
        "terms": len(vocabulary.terms),
        "drug_terms": len(vocabulary.drug_terms),
        "corpus_signature": vocabulary.corpus_signature,
    }
