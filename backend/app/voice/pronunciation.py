"""The pronunciation dictionary (11E.4).

``data/pronunciations.csv`` holds curated IPA (and a plain respelling) for
drug and condition names. ``build_lexicon`` keeps only the entries that occur
in the *corpus vocabulary*, so the dictionary shipped to the voice is derived
from what the corpus talks about, and ``coverage`` reports honestly how much
of the corpus drug vocabulary it covers — the rest is left to the engine.

* ElevenLabs consumes the W3C PLS lexicon (``to_pls``); it is uploaded once
  per process and referenced on every synthesis request.
* The offline engines cannot take a lexicon, so the respelling is substituted
  into the text sent to TTS instead (``apply_respellings``) — the words the
  transcript shows are untouched.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from app.voice.vocabulary import VoiceVocabulary

_DATA = Path(__file__).resolve().parent / "data" / "pronunciations.csv"


@dataclass(frozen=True, slots=True)
class Pronunciation:
    term: str
    ipa: str
    respelling: str


@lru_cache
def load_pronunciations(path: Path = _DATA) -> dict[str, Pronunciation]:
    out: dict[str, Pronunciation] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            term = row["term"].strip().lower()
            out[term] = Pronunciation(term, row["ipa"].strip(), row["respelling"].strip())
    return out


@dataclass(slots=True)
class Lexicon:
    entries: list[Pronunciation]
    corpus_drug_terms: int
    covered_drug_terms: int

    @property
    def coverage(self) -> float:
        if self.corpus_drug_terms == 0:
            return 0.0
        return self.covered_drug_terms / self.corpus_drug_terms

    def to_pls(self) -> str:
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<lexicon version="1.0" xmlns="http://www.w3.org/2005/01/pronunciation-lexicon"',
            '         alphabet="ipa" xml:lang="en-US">',
        ]
        for entry in self.entries:
            lines.append(
                f"  <lexeme><grapheme>{escape(entry.term)}</grapheme>"
                f"<phoneme>{escape(entry.ipa)}</phoneme></lexeme>"
            )
        lines.append("</lexicon>")
        return "\n".join(lines)

    def apply_respellings(self, text: str) -> str:
        """Substitute respellings for engines without lexicon support."""
        if not self.entries:
            return text
        out = text
        for entry in self.entries:
            out = re.sub(rf"\b{re.escape(entry.term)}\b", entry.respelling, out, flags=re.I)
        return out


def build_lexicon(vocabulary: VoiceVocabulary) -> Lexicon:
    table = load_pronunciations()
    corpus_terms = set(vocabulary.all_terms)
    entries = [p for term, p in table.items() if term in corpus_terms]
    if not corpus_terms:
        # No corpus (tests without a DB): the curated table stands on its own.
        entries = list(table.values())
    drug_terms = {t for t in vocabulary.drug_terms if t in corpus_terms} or set(
        vocabulary.drug_terms
    )
    covered = sum(1 for t in drug_terms if t in table)
    return Lexicon(sorted(entries, key=lambda e: e.term), len(drug_terms), covered)
