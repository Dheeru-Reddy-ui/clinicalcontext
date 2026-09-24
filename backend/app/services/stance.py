"""Where each cited source stands relative to the answer.

The evidence timeline colours every source green (supports), red (opposes)
or grey (neutral). Stance used to exist only when a contradiction was
detected, so an ordinary answer drew every source grey — the timeline said
nothing. Every citation now carries a stance, decided from what the answer
does with it:

- a source the answer cites for a claim **supports** it;
- a source on the opposing side of a detected contradiction, or cited by a
  sentence that argues against the answer's own conclusion, **opposes** it;
- a source that was retrieved and shown but never cited is **neutral**
  background.

Deterministic, so the same answer always draws the same timeline.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from app.schemas.answer import Citation, Contradiction

Stance = Literal["supports", "opposes", "neutral"]

# "[1]", "[1, 3]", "[2-4]" and the en-dash range a model may write.
_MARKER = re.compile(r"\[(\d+(?:\s*[,\-" + "\N{EN DASH}" + r"]\s*\d+)*)\]")
_RANGE = re.compile(r"\s*[\-" + "\N{EN DASH}" + r"]\s*")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[(\"'])")
_POSITIVE = re.compile(
    r"\b(?:recommend(?:s|ed)?|first[-\s]?line|preferred|superior|effective|"
    r"benefit(?:s|ed)?|improv(?:e|es|ed)|reduc(?:e|es|ed)\s+(?:the\s+)?(?:risk|mortality)|"
    r"indicated)\b",
    re.I,
)
# Conclusions against, not caveats: "avoid in eGFR < 30" qualifies an answer
# that recommends a drug; it does not oppose it, so it is not listed here.
_NEGATIVE = re.compile(
    r"\b(?:not\s+recommend(?:ed)?|recommends?\s+against|"
    r"no\s+(?:significant\s+|clear\s+)?benefit|insufficient\s+evidence|inferior|"
    r"no\s+significant|did\s+not\s+(?:reduce|improve)|lack\s+of\s+evidence|"
    r"against\s+(?:the\s+)?use|ineffective|not\s+effective)\b",
    re.I,
)
_OPPOSING_POSITION = re.compile(r"against|not\s+support|oppos|no\s+benefit|inferior", re.I)


def polarity(text: str) -> int:
    """+1 recommends/benefits, -1 against/no benefit, 0 neither. Negation
    wins: "not recommended" contains "recommended"."""
    if _NEGATIVE.search(text):
        return -1
    if _POSITIVE.search(text):
        return 1
    return 0


def markers_in(sentence: str) -> set[int]:
    """Every marker number in ``sentence``, ranges and lists expanded."""
    found: set[int] = set()
    for group in _MARKER.findall(sentence):
        for part in re.split(r"\s*,\s*", group):
            bounds = _RANGE.split(part)
            if len(bounds) == 2 and bounds[0].isdigit() and bounds[1].isdigit():
                low, high = int(bounds[0]), int(bounds[1])
                if 0 < high - low < 20:
                    found.update(range(low, high + 1))
                    continue
            if part.strip().isdigit():
                found.add(int(part.strip()))
    return found


def stances_for(
    answer: str, markers: Sequence[int], contradiction: Contradiction | None = None
) -> dict[int, Stance]:
    """marker → stance for every marker in ``markers``."""
    result: dict[int, Stance] = dict.fromkeys(markers, "neutral")
    placed: set[int] = set()

    if contradiction is not None and contradiction.detected and contradiction.positions:
        # A position that says so ("recommends against", "no benefit") is the
        # opposing side; when none says so, the answer leads with the first.
        labelled = any(_OPPOSING_POSITION.search(p.stance) for p in contradiction.positions)
        for index, position in enumerate(contradiction.positions):
            opposing = bool(_OPPOSING_POSITION.search(position.stance)) if labelled else index > 0
            for marker in position.markers:
                if marker in result:
                    result[marker] = "opposes" if opposing else "supports"
                    placed.add(marker)

    sentences = [s for s in _SENTENCE.split(answer.strip()) if s]
    lead = next((polarity(s) for s in sentences if polarity(s) != 0), 0)
    for sentence in sentences:
        cited = markers_in(sentence) - placed
        if not cited:
            continue
        against_lead = lead != 0 and polarity(sentence) == -lead
        for marker in cited:
            if marker not in result:
                continue
            if against_lead:
                result[marker] = "opposes"
            elif result[marker] == "neutral":
                result[marker] = "supports"
    return result


def assign_stances(
    answer: str, citations: list[Citation], contradiction: Contradiction | None
) -> list[Citation]:
    """The citations with ``stance`` filled in (copies; inputs untouched)."""
    stances = stances_for(answer, [c.marker for c in citations], contradiction)
    return [c.model_copy(update={"stance": stances.get(c.marker, "neutral")}) for c in citations]
