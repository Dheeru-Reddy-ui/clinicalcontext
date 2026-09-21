"""Post-STT medical-term correction (11B.4).

Runs on the *final* transcript only. Every token the recognizer produced that
is not itself a known term is fuzzy-matched (edit distance + a light phonetic
key) against the corpus vocabulary; near-misses are replaced ("a pixaban" →
"apixaban", "hydroxygeny" → "hydroxyzine") and every replacement is logged and
returned, so a correction is never invisible — the LASA gate treats corrected
drug names as low-confidence by construction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog
from rapidfuzz import fuzz, process

from app.voice.vocabulary import VoiceVocabulary

logger = structlog.stdlib.get_logger("app.voice.correction")

# Ordinary words the fuzzy matcher must never "correct" into a drug name.
_PROTECTED = frozenset(
    [
        "about",
        "above",
        "after",
        "again",
        "against",
        "because",
        "before",
        "being",
        "below",
        "between",
        "both",
        "could",
        "does",
        "doing",
        "during",
        "first",
        "found",
        "great",
        "group",
        "having",
        "here",
        "however",
        "into",
        "large",
        "later",
        "little",
        "might",
        "never",
        "number",
        "often",
        "other",
        "people",
        "place",
        "point",
        "rather",
        "really",
        "right",
        "should",
        "since",
        "small",
        "something",
        "still",
        "their",
        "there",
        "these",
        "thing",
        "think",
        "those",
        "three",
        "through",
        "under",
        "until",
        "using",
        "very",
        "water",
        "where",
        "which",
        "while",
        "whole",
        "within",
        "without",
        "world",
        "would",
        "years",
        "young",
        "patient",
        "patients",
        "treatment",
        "treatments",
        "therapy",
        "evidence",
        "guideline",
        "guidelines",
        "compared",
        "compare",
        "versus",
        "disease",
        "disorder",
        "chronic",
        "acute",
        "primary",
        "secondary",
        "prevention",
        "effective",
        "efficacy",
        "safety",
        "adults",
        "children",
        "older",
        "adult",
        "recommended",
        "recommend",
        "management",
        "managed",
        "manage",
        "diagnosis",
        "diagnosed",
        "risk",
        "reduce",
        "reduction",
        "outcome",
        "outcomes",
        "first-line",
        "second-line",
        "line",
        "dose",
        "dosing",
        "hypertension",
        "diabetes",
        "stroke",
        "heart",
        "failure",
        "kidney",
        "liver",
        "cancer",
        "infection",
        "warfare",
    ]
)

_ALPHA = re.compile(r"^[a-z][a-z-]+$")
# Function words that never begin a glued drug name.
_GLUE_STOP = frozenset(
    [
        "does",
        "is",
        "are",
        "the",
        "and",
        "or",
        "of",
        "to",
        "for",
        "in",
        "on",
        "with",
        "what",
        "which",
        "how",
        "should",
        "can",
        "do",
        "did",
        "was",
        "were",
        "has",
        "have",
        "it",
        "its",
        "be",
        "by",
        "as",
        "at",
        "an",
        "that",
        "this",
        "if",
        "not",
        "no",
        "than",
        "then",
        "when",
    ]
)
# Join fragments may be a single letter ("a pixaban" → "apixaban").
_FRAGMENT = re.compile(r"^[a-z][a-z-]*$")


@dataclass(slots=True)
class Word:
    text: str
    confidence: float
    corrected: bool = False
    original: str | None = None


@dataclass(slots=True)
class Correction:
    original: str
    corrected: str
    score: float


@dataclass(slots=True)
class CorrectionResult:
    words: list[Word]
    corrections: list[Correction] = field(default_factory=list)

    @property
    def text(self) -> str:
        return join_words(self.words)


def join_words(words: list[Word]) -> str:
    out = " ".join(w.text for w in words)
    return re.sub(r"\s+([,.?!;:])", r"\1", out).strip()


def phonetic_key(token: str) -> str:
    """A small, deterministic sound key (Metaphone-flavoured, not Metaphone)."""
    t = token.lower()
    t = re.sub(r"[^a-z]", "", t)
    if not t:
        return ""
    t = t.replace("ph", "f").replace("ck", "k").replace("qu", "k").replace("x", "ks")
    t = re.sub(r"c(?=[eiy])", "s", t).replace("c", "k")
    t = t.replace("z", "s").replace("y", "i").replace("w", "v").replace("th", "t")
    t = t.replace("dg", "j").replace("g", "k")
    head, tail = t[0], t[1:]
    tail = re.sub(r"[aeiou]", "", tail)
    key = head + tail
    return re.sub(r"(.)\1+", r"\1", key)


def _score(token: str, term: str) -> float:
    literal = fuzz.ratio(token, term)
    phonetic = fuzz.ratio(phonetic_key(token), phonetic_key(term))
    return max(literal, phonetic - 4.0)


def _strip(token: str) -> tuple[str, str]:
    """Separate trailing punctuation so it can be re-attached after correction.
    Leading hyphens/punctuation ("-colagulation") are dropped outright."""
    match = re.match(r"^[-\u2013\u2014.,;:]*(.*?)([,.?!;:]*)$", token)
    if match is None:
        return token, ""
    return match.group(1), match.group(2)


class MedicalTermCorrector:
    def __init__(
        self,
        vocabulary: VoiceVocabulary,
        *,
        single_cutoff: float = 82.0,
        short_cutoff: float = 88.0,
        join_cutoff: float = 78.0,
    ) -> None:
        self._vocabulary = vocabulary
        # A recognizer token is corrected to a single word; multi-word MeSH
        # headings ("cardiovascular agents") are not correction targets.
        self._terms = [t for t in vocabulary.terms if " " not in t]
        self._drug_terms = sorted(t for t in vocabulary.drug_terms if " " not in t)
        self._single_cutoff = single_cutoff
        self._short_cutoff = short_cutoff
        self._join_cutoff = join_cutoff

    def _best(self, token: str, pool: list[str], cutoff: float) -> tuple[str, float] | None:
        if not pool:
            return None
        found = process.extractOne(
            token, pool, scorer=fuzz.ratio, score_cutoff=max(cutoff - 15.0, 50.0)
        )
        candidates: list[tuple[str, float]] = []
        if found is not None:
            candidates.append((found[0], _score(token, found[0])))
        # Phonetic rescue: the literal top hit may not be the phonetic one.
        key = phonetic_key(token)
        if key:
            for term in pool:
                if abs(len(term) - len(token)) > 4:
                    continue
                if fuzz.ratio(key, phonetic_key(term)) >= 90:
                    candidates.append((term, _score(token, term)))
        if not candidates:
            return None
        term, score = max(candidates, key=lambda item: item[1])
        return (term, score) if score >= cutoff else None

    def correct(self, words: list[Word]) -> CorrectionResult:
        out: list[Word] = []
        corrections: list[Correction] = []
        i = 0
        while i < len(words):
            # 1. Multi-token joins ("a pixaban", "clone it in") → one drug term.
            joined = self._try_join(words, i)
            if joined is not None:
                term, score, span = joined
                originals = [w.text for w in words[i : i + span]]
                _, punct = _strip(originals[-1])
                confidence = min(w.confidence for w in words[i : i + span])
                out.append(
                    Word(term + punct, confidence, corrected=True, original=" ".join(originals))
                )
                corrections.append(Correction(" ".join(originals), term, round(score, 1)))
                i += span
                continue
            # 2. Single-token near-miss.
            word = words[i]
            core, punct = _strip(word.text.lower())
            replacement = self._single(core)
            if replacement is not None:
                term, score = replacement
                out.append(Word(term + punct, word.confidence, corrected=True, original=word.text))
                corrections.append(Correction(word.text, term, round(score, 1)))
            else:
                out.append(word)
            i += 1
        if corrections:
            logger.info(
                "voice_transcript_corrected",
                corrections=[(c.original, c.corrected, c.score) for c in corrections],
            )
        return CorrectionResult(out, corrections)

    def _single(self, core: str) -> tuple[str, float] | None:
        if not _ALPHA.match(core) or len(core) < 5 or core in _PROTECTED:
            return None
        if self._vocabulary.is_known(core):
            return None
        cutoff = self._single_cutoff if len(core) >= 7 else self._short_cutoff
        return self._best(core, self._terms, cutoff)

    def _try_join(self, words: list[Word], start: int) -> tuple[str, float, int] | None:
        for span in (2, 3):
            if start + span > len(words):
                continue
            parts = [_strip(w.text.lower())[0] for w in words[start : start + span]]
            if not all(_FRAGMENT.match(p) for p in parts):
                continue
            # A join starts with a fragment, never with a function word
            # ("does a pixaban" must glue "a pixaban", not "does a pixaban").
            if parts[0] in _GLUE_STOP:
                continue
            # Only glue short or unknown fragments — never two real terms.
            if any(self._vocabulary.is_known(p) and len(p) > 5 for p in parts):
                continue
            # Two real words that form a known phrase ("chest pain") are never
            # a broken drug name.
            if self._vocabulary.is_known(" ".join(parts)):
                continue
            merged = "".join(parts)
            if len(merged) < 8:
                continue
            # Literal similarity only — the phonetic rescue is for single tokens.
            # A one-letter fragment ("a pixaban") may glue at a lower cutoff.
            cutoff = self._join_cutoff if min(len(p) for p in parts) <= 2 else 85.0
            found = process.extractOne(
                merged, self._drug_terms, scorer=fuzz.ratio, score_cutoff=cutoff
            )
            if found is not None:
                return found[0], float(found[1]), span
            # Same sound, loose spelling ("clone ispum" → clonazepam,
            # "hydrogels in" → hydralazine): the sound keys must be all but
            # identical — the guards above already keep "chest pain" out, and
            # the LASA gate confirms any look-alike name this produces.
            key = phonetic_key(merged)
            if key:
                best: tuple[float, float, str] | None = None
                for term in self._drug_terms:
                    sound = fuzz.ratio(key, phonetic_key(term))
                    if sound < 90:
                        continue
                    literal = fuzz.ratio(merged, term)
                    if literal >= 60 and (best is None or (sound, literal) > best[:2]):
                        best = (sound, literal, term)
                if best is not None:
                    return best[2], float(best[1]), span
        return None
