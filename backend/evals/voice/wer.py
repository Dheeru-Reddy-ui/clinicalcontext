"""Word error rate and medical-term error rate.

WER is the standard Levenshtein edit distance over normalized word
sequences. The medical-term error rate is the fraction of a fixture's
listed clinical terms (drug names, conditions) that do not appear in the
final transcript — the number the 11B gate is about: hearing "pneumonia" as
"pneumonia" matters more than hearing "the" as "a".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WORD = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")
_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}  # fmt: skip


def normalize(text: str) -> list[str]:
    words = _WORD.findall(text.lower().replace(chr(0x2019), "'"))
    return [_NUMBER_WORDS.get(w, w) for w in words]


def edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    rows = len(reference) + 1
    cols = len(hypothesis) + 1
    previous = list(range(cols))
    for i in range(1, rows):
        current = [i] + [0] * (cols - 1)
        for j in range(1, cols):
            cost = 0 if reference[i - 1] == hypothesis[j - 1] else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
        previous = current
    return previous[-1]


@dataclass(slots=True)
class WerResult:
    reference_words: int
    errors: int
    medical_terms: int
    medical_misses: list[str]

    @property
    def wer(self) -> float | None:
        return round(self.errors / self.reference_words, 4) if self.reference_words else None

    @property
    def medical_term_error_rate(self) -> float | None:
        if not self.medical_terms:
            return None
        return round(len(self.medical_misses) / self.medical_terms, 4)


def score(reference: str, hypothesis: str, medical_terms: list[str]) -> WerResult:
    ref = normalize(reference)
    hyp = normalize(hypothesis)
    padded = f" {' '.join(hyp)} "
    misses = [term for term in medical_terms if f" {' '.join(normalize(term))} " not in padded]
    return WerResult(len(ref), edit_distance(ref, hyp), len(medical_terms), misses)
