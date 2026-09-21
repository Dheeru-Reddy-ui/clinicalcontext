"""The LASA confirmation gate (11D.3).

After the final transcript and the correction pass, every token that lands
on (or near) the ISMP confusion table is examined. The agent confirms — by
voice and on screen — rather than answering when:

* the recognizer's confidence for that word is below the floor,
* the word was produced by the correction pass (a fuzzy guess, by definition),
* the heard token is not an exact table name, or
* the pair's other name is a plausible reading of what was heard and the
  recognizer was not highly confident.

One extra turn; never a silent substitution. Names the user has already
confirmed in this session are trusted for the rest of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.voice.correction import Word, _strip
from app.voice.lasa import LasaEntry, LasaTable, confirmation_prompt


@dataclass(slots=True)
class LasaDecision:
    index: int
    heard: str
    options: list[LasaEntry]
    prompt: str
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "requested": True,
            "heard": self.heard,
            "options": [o.name for o in self.options],
            "reasons": self.reasons,
        }


def lasa_gate(
    words: list[Word],
    lasa: LasaTable,
    *,
    confidence_floor: float,
    trusted: frozenset[str] = frozenset(),
    high_confidence: float = 0.9,
    alternative_plausible_score: float = 60.0,
) -> LasaDecision | None:
    for index, word in enumerate(words):
        core, _ = _strip(word.text.lower())
        if len(core) < 5 or not core.isalpha():
            continue
        match = lasa.match(core)
        if match is None or match.candidate.name in trusted:
            continue
        reasons: list[str] = []
        exact = core == match.candidate.name
        if not exact:
            reasons.append("inexact_token")
        if word.corrected:
            reasons.append("fuzzy_corrected")
        if word.confidence < confidence_floor:
            reasons.append(f"low_confidence:{word.confidence:.2f}")
        alternative_plausible = (
            match.alternative_score >= alternative_plausible_score
            or abs(match.candidate_score - match.alternative_score) <= 15
        )
        if alternative_plausible and word.confidence < high_confidence:
            reasons.append("plausible_alternative")
        if not reasons:
            continue
        options = [match.candidate, match.alternative]
        return LasaDecision(
            index=index,
            heard=core,
            options=options,
            prompt=confirmation_prompt(options[0], options[1]),
            reasons=reasons,
        )
    return None


def apply_choice(words: list[Word], decision: LasaDecision, chosen: LasaEntry) -> list[Word]:
    """Replace the confirmed token in the transcript (punctuation preserved)."""
    out = list(words)
    original = out[decision.index]
    _, punct = _strip(original.text)
    out[decision.index] = Word(
        chosen.name + punct,
        1.0,
        corrected=original.text.lower() != chosen.name,
        original=original.text,
    )
    return out
