"""The look-alike / sound-alike confusion table (11B.3) and confirmation gate (11D.3).

``data/lasa_pairs.csv`` is transcribed from the ISMP *List of Confused Drug
Names* (pairs the Institute for Safe Medication Practices reports as
confused in practice), each name carrying the short class description the
agent speaks when it asks. Mishearing across one of these pairs is a safety
failure, not a UX bug — so a drug on this table is never silently accepted
when the recognizer was unsure about it.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz

_DATA = Path(__file__).resolve().parent / "data" / "lasa_pairs.csv"


@dataclass(frozen=True, slots=True)
class LasaEntry:
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class LasaMatch:
    heard: str
    candidate: LasaEntry
    alternative: LasaEntry
    # How close the heard token is to the candidate vs. the alternative, 0-100.
    candidate_score: float
    alternative_score: float


class LasaTable:
    def __init__(self, pairs: list[tuple[LasaEntry, LasaEntry]]) -> None:
        self._pairs = pairs
        self._by_name: dict[str, list[tuple[LasaEntry, LasaEntry]]] = {}
        for a, b in pairs:
            self._by_name.setdefault(a.name, []).append((a, b))
            self._by_name.setdefault(b.name, []).append((b, a))

    @classmethod
    def load(cls, path: Path = _DATA) -> LasaTable:
        pairs: list[tuple[LasaEntry, LasaEntry]] = []
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                pairs.append(
                    (
                        LasaEntry(row["drug_a"].strip().lower(), row["class_a"].strip()),
                        LasaEntry(row["drug_b"].strip().lower(), row["class_b"].strip()),
                    )
                )
        return cls(pairs)

    def __len__(self) -> int:
        return len(self._pairs)

    def names(self) -> list[str]:
        return sorted(self._by_name)

    def alternatives(self, name: str) -> list[tuple[LasaEntry, LasaEntry]]:
        return self._by_name.get(name.lower(), [])

    def is_confusable(self, name: str) -> bool:
        return name.lower() in self._by_name

    def match(self, heard: str) -> LasaMatch | None:
        """Best confusion pair for a heard token, or None if it is nowhere near
        the table. Multi-word names are matched on the space-joined form."""
        token = heard.lower().strip(" .,?!")
        best: LasaMatch | None = None
        for name, entries in self._by_name.items():
            score = fuzz.ratio(token, name)
            if score < 75:
                continue
            for candidate, alternative in entries:
                alt_score = fuzz.ratio(token, alternative.name)
                match = LasaMatch(token, candidate, alternative, score, alt_score)
                if best is None or match.candidate_score > best.candidate_score:
                    best = match
        return best


@lru_cache
def default_lasa_table() -> LasaTable:
    return LasaTable.load()


def confirmation_prompt(a: LasaEntry, b: LasaEntry) -> str:
    """The spoken confirmation line — one extra turn, never a silent guess."""
    return f"Just to be safe — {a.name}, {a.description}, or {b.name}, {b.description}?"


def resolve_choice(spoken: str, options: list[LasaEntry]) -> LasaEntry | None:
    """Map a spoken/tapped reply onto one of the offered names.

    Accepts the name itself, a fuzzy rendering of it, an ordinal ("the first
    one", "second"), or a distinguishing word from the class description.
    """
    text = spoken.lower().strip(" .,?!")
    if not text:
        return None
    exact = [o for o in options if o.name == text or f" {o.name} " in f" {text} "]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        # "esomeprazole" contains "omeprazole": the longest name named wins.
        return max(exact, key=lambda o: len(o.name))
    ordinals = {
        "first": 0,
        "1": 0,
        "one": 0,
        "former": 0,
        "second": 1,
        "2": 1,
        "two": 1,
        "latter": 1,
    }
    for word, index in ordinals.items():
        if f" {word} " in f" {text} " and index < len(options):
            return options[index]
    scored: list[tuple[float, LasaEntry]] = []
    for option in options:
        score = max(fuzz.partial_ratio(text, option.name), fuzz.ratio(text, option.name))
        for desc_word in option.description.lower().split():
            if len(desc_word) > 4 and desc_word in text.split():
                score = max(score, 90.0)
        scored.append((score, option))
    scored.sort(key=lambda item: -item[0])
    if scored and scored[0][0] >= 70 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 5):
        return scored[0][1]
    return None
