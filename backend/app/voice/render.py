"""Voice answer rendering (11E): the same grounded answer, restructured for the ear.

* ``[n]`` markers do not speak. Each sentence's sources become natural
  attribution built from the citation metadata ("according to a 2023
  meta-analysis in JAMA"); the on-screen transcript keeps the chips.
* Doses, percentages, intervals and p-values are rendered speakably
  ("two point five milligrams twice daily", "a 21 percent relative risk
  reduction"); p-value notation is never read raw.
* Long answers are not monologues: the first two to three sentences are the
  core, the rest waits behind a spoken offer (progressive disclosure), and a
  detected contradiction is offered as a walk-through of both positions.
* Contradiction, abstention and guardrail outcomes have spoken forms that are
  short, honest and unapologetic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from app.schemas.answer import AnswerResult, Citation, Contradiction
from app.voice.segmenter import split_sentences

_MARKER = re.compile(r"\s*\[(\d+(?:\s*,\s*\d+)*)\]")
# The extractive generator's framing — a sentence of its own, or (older
# form) a prefix joined to the first claim by a colon. Framing is not spoken.
_PREFACE = re.compile(
    r"^(?:based on the retrieved (?:literature|evidence)"
    r"(?:,\s*the evidence is as follows\.?|,\s*the sources disagree, so this is a conflict[.:]?)?"
    r"[,:]?\s*)",
    re.I,
)

_STUDY_TYPE_SPOKEN: dict[str, str] = {
    "systematic_review": "a systematic review",
    "meta_analysis": "a meta-analysis",
    "randomized_controlled_trial": "a randomized trial",
    "cohort_study": "a cohort study",
    "case_control_study": "a case-control study",
    "case_series": "a case series",
    "case_report": "a case report",
    "clinical_guideline": "the guideline",
    "narrative_review": "a review",
    "other": "a study",
}

SpokenKind = Literal[
    "answer", "offer", "refusal", "escalation", "abstention", "confirmation", "mask", "system"
]


@dataclass(slots=True)
class SpokenSentence:
    text: str  # transcript form (markers kept)
    spoken: str  # what goes to TTS
    markers: list[int] = field(default_factory=list)
    kind: SpokenKind = "answer"


@dataclass(slots=True)
class SpokenAnswer:
    core: list[SpokenSentence]
    remainder: list[SpokenSentence]
    offer: SpokenSentence | None
    offer_kind: Literal["walkthrough", "more"] | None


# -- numbers and units (11E.5) ----------------------------------------------------------

_UNIT_WORDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?<=\d)\s*%"), " percent"),
    (re.compile(r"\bmg/dl\b", re.I), "milligrams per deciliter"),
    (re.compile(r"\bmmol/l\b", re.I), "millimoles per liter"),
    (re.compile(r"\bmg/kg\b", re.I), "milligrams per kilogram"),
    (re.compile(r"\bmcg\b|\bµg\b|\bug\b"), "micrograms"),
    (re.compile(r"(?<=\d)\s*mg\b"), " milligrams"),
    (re.compile(r"(?<=\d)\s*g\b"), " grams"),
    (re.compile(r"(?<=\d)\s*kg\b"), " kilograms"),
    (re.compile(r"(?<=\d)\s*ml\b", re.I), " milliliters"),
    (re.compile(r"(?<=\d)\s*mmhg\b", re.I), " millimeters of mercury"),
    (re.compile(r"(?<=\d)\s*iu\b", re.I), " international units"),
    (re.compile(r"(?<=\d)\s*(?:hrs?|h)\b"), " hours"),
    (re.compile(r"(?<=\d)\s*min\b"), " minutes"),
    (re.compile(r"(?<=\d)\s*(?:wks?)\b"), " weeks"),
    (re.compile(r"(?<=\d)\s*mo\b"), " months"),
    (re.compile(r"(?<=\d)\s*yrs?\b"), " years"),
    (re.compile(r"\bq\s*(\d+)\s*h(?:ours)?\b", re.I), r"every \1 hours"),
    (re.compile(r"\b(?:bid|b\.i\.d\.)\b", re.I), "twice daily"),
    (re.compile(r"\b(?:tid|t\.i\.d\.)\b", re.I), "three times daily"),
    (re.compile(r"\b(?:qid|q\.i\.d\.)\b", re.I), "four times daily"),
    (re.compile(r"\b(?:qd|od|q\.d\.)\b"), "once daily"),
    (re.compile(r"\bPO\b"), "by mouth"),
    (re.compile(r"\bIV\b"), "intravenous"),
    (re.compile(r"\bIM\b"), "intramuscular"),
    (re.compile(r"\b(?:SC|SQ|subQ)\b"), "subcutaneous"),
    (re.compile(r"\bHbA1c\b", re.I), "hemoglobin A1c"),
    (re.compile(r"\bT2DM\b"), "type 2 diabetes"),
    (re.compile(r"\bNNT\b"), "number needed to treat"),
    (re.compile(r"\bARR\b"), "absolute risk reduction"),
    (re.compile(r"\bRRR\b"), "relative risk reduction"),
    (re.compile(r"\bCI\b"), "confidence interval"),
    (re.compile(r"\bHR\s*(?=[=:<>]?\s*\d)"), "hazard ratio "),
    (re.compile(r"\bRR\s*(?=[=:<>]?\s*\d)"), "relative risk "),
    (re.compile(r"\bOR\s*(?=[=:<>]?\s*\d)"), "odds ratio "),
    (re.compile(r"\be\.g\.,?\s*", re.I), "for example, "),
    (re.compile(r"\bi\.e\.,?\s*", re.I), "that is, "),
    (re.compile(r"\bvs\.?\b", re.I), "versus"),
    (re.compile(r"\bet al\.?", re.I), "and colleagues"),
]
_P_VALUE = re.compile(r"\b[pP]\s*([<>=≤≥])\s*(0?\.\d+|\d+(?:\.\d+)?)")
_RANGE = re.compile(r"(?<=\d)\s*[\u2013\u2014-]\s*(?=\d)")
_SYMBOLS: list[tuple[str, str]] = [
    ("≥", " at least "),
    ("≤", " at most "),
    ("→", " to "),
    ("±", " plus or minus "),
    ("\u00d7", " times "),
    (" x ", " times "),
    ("<", " less than "),
    (">", " greater than "),
    ("=", " equals "),
    ("/", " per "),
]


def speakable(text: str) -> str:
    """Render numbers, units and symbols so a TTS voice says them correctly."""
    out = text
    # Never speak a p-value raw ("p < 0.05" → "p less than 0.05").
    out = _P_VALUE.sub(
        lambda m: (
            "p "
            + {
                "<": "less than",
                ">": "greater than",
                "=": "equals",
                "≤": "at most",
                "≥": "at least",
            }[m.group(1)]
            + " "
            + m.group(2)
        ),
        out,
    )
    out = _RANGE.sub(" to ", out)
    for pattern, replacement in _UNIT_WORDS:
        out = pattern.sub(replacement, out)
    # Singularize "1 milligrams".
    out = re.sub(
        r"\b1 (milligrams|grams|kilograms|milliliters|hours|minutes|weeks|months|years)\b",
        lambda m: "1 " + m.group(1)[:-1],
        out,
    )
    for symbol, spoken in _SYMBOLS:
        if symbol in out:
            out = out.replace(symbol, spoken)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


# -- citations → attribution (11E.2) --------------------------------------------------


def _year(citation: Citation) -> str | None:
    if citation.publication_date and len(citation.publication_date) >= 4:
        return citation.publication_date[:4]
    return None


def describe_source(citation: Citation) -> str:
    kind = _STUDY_TYPE_SPOKEN.get(citation.study_type or "other", "a study")
    year = _year(citation)
    journal = (citation.journal or "").strip()
    if kind == "the guideline":
        parts = ["the"]
        if year:
            parts.append(year)
        if journal:
            parts.append(journal)
        parts.append("guideline")
        return " ".join(parts)
    article, noun = kind.split(" ", 1)
    parts = [article]
    if year:
        parts.append(year)
    parts.append(noun)
    if journal:
        parts.append(f"in {journal}")
    return " ".join(parts)


def attribution(markers: list[int], citations: dict[int, Citation]) -> str:
    """ "according to a 2023 meta-analysis in JAMA and the 2024 ESC guideline"."""
    described: list[str] = []
    for marker in markers:
        citation = citations.get(marker)
        if citation is None:
            continue
        text = describe_source(citation)
        if text not in described:
            described.append(text)
    if not described:
        return ""
    if len(described) == 1:
        return f"according to {described[0]}"
    if len(described) == 2:
        return f"according to {described[0]} and {described[1]}"
    return f"according to {described[0]}, {described[1]}, and {len(described) - 2} more"


def _lower_first(sentence: str) -> str:
    if len(sentence) >= 2 and sentence[0].isupper() and sentence[1].islower():
        return sentence[0].lower() + sentence[1:]
    return sentence


def render_sentence(
    sentence: str, citations: dict[int, Citation], *, previous_markers: list[int] | None = None
) -> SpokenSentence:
    markers = [int(m) for group in _MARKER.findall(sentence) for m in group.split(",")]
    body = _MARKER.sub("", sentence).strip()
    body = _PREFACE.sub("", body).strip()
    if not body:
        return SpokenSentence(sentence, "", markers)
    spoken_body = speakable(body)
    if markers and markers != (previous_markers or []):
        attributed = attribution(markers, citations)
        if attributed:
            spoken_body = f"{attributed[0].upper()}{attributed[1:]}, {_lower_first(spoken_body)}"
    if not spoken_body.endswith((".", "?", "!")):
        spoken_body += "."
    return SpokenSentence(sentence, spoken_body, markers)


# -- whole answers ----------------------------------------------------------------------

CORE_SENTENCES = 3
OFFER_WALKTHROUGH = "There's conflicting evidence on this — want me to walk through both positions?"
OFFER_MORE = "There's more detail behind that — want me to go on?"


def positions_spoken(
    contradiction: Contradiction, citations: dict[int, Citation]
) -> list[SpokenSentence]:
    out: list[SpokenSentence] = []
    for i, position in enumerate(contradiction.positions):
        label = "One position" if i == 0 else "The other position"
        year = f", from {position.year}," if position.year else ""
        sources = attribution(position.markers, citations)
        stance = position.stance.rstrip(".")
        text = f"{label}{year} holds that {stance} [{', '.join(str(m) for m in position.markers)}]."
        spoken = f"{label}{year} holds that {speakable(stance)}"
        if sources:
            spoken += f", {sources}"
        out.append(SpokenSentence(text, spoken + ".", list(position.markers)))
    if contradiction.axis == "temporal":
        line = (
            "The difference tracks publication year — the more recent sources reach "
            "a different conclusion."
        )
        out.append(SpokenSentence(line, line, []))
    elif contradiction.axis in ("population", "endpoint"):
        line = f"The difference appears to come from different {contradiction.axis}s studied."
        out.append(SpokenSentence(line, line, []))
    return out


def render_answer(result: AnswerResult) -> SpokenAnswer:
    citations = {c.marker: c for c in result.citations}
    if result.abstained:
        line = abstention_spoken(result)
        return SpokenAnswer([SpokenSentence(line, line, [], "abstention")], [], None, None)

    sentences = split_sentences(result.answer)
    rendered: list[SpokenSentence] = []
    previous: list[int] = []
    for sentence in sentences:
        spoken = render_sentence(sentence, citations, previous_markers=previous)
        if spoken.spoken:
            rendered.append(spoken)
            previous = spoken.markers

    if result.contradiction.detected:
        opener = "The sources disagree on this."
        core = [SpokenSentence(opener, opener, []), *rendered[:1]]
        remainder = positions_spoken(result.contradiction, citations) + rendered[1:]
        offer = SpokenSentence(OFFER_WALKTHROUGH, OFFER_WALKTHROUGH, [], "offer")
        return SpokenAnswer(core, remainder, offer, "walkthrough")

    core, remainder = rendered[:CORE_SENTENCES], rendered[CORE_SENTENCES:]
    if remainder:
        return SpokenAnswer(
            core, remainder, SpokenSentence(OFFER_MORE, OFFER_MORE, [], "offer"), "more"
        )
    return SpokenAnswer(core, [], None, None)


def abstention_spoken(result: AnswerResult) -> str:
    """Competent, not apologetic."""
    if result.retrieval_grade == "irrelevant":
        return (
            "I don't have literature I can cite on that, so I won't guess. "
            "Try naming the population and the intervention more specifically."
        )
    return (
        "The evidence I can cite on that is too thin to give you a reliable answer, "
        "so I'm not going to guess. Narrowing the question may help."
    )


# -- guardrail and system lines ------------------------------------------------------

SPOKEN_PHI = (
    "That question seems to include patient details, and I can't take patient "
    "information. Ask about the literature in general and I'll help."
)
SPOKEN_SCOPE = (
    "I'm built for questions about the medical literature — I can't help with that, "
    "but ask me anything about clinical evidence."
)
SPOKEN_INJECTION = (
    "I can't change how I work, but I'm happy to answer a question about the clinical evidence."
)
SPOKEN_RED_FLAG = (
    "First — if this is about someone right now, it could be an emergency: call your "
    "local emergency number. Here is what the literature says."
)
SPOKEN_MASK = "Checking the guidelines."
SPOKEN_RESUME = "Continuing."
SPOKEN_NOTHING_TO_RESUME = "There's nothing left to continue — ask me something else."
SPOKEN_CONFIRM_FAILED = "I didn't catch that. Which one did you mean?"


def guardrail_spoken(blocked_by: str | None, code: str | None) -> str:
    if blocked_by == "phi":
        return SPOKEN_PHI
    if code == "refuse_prompt_injection":
        return SPOKEN_INJECTION
    return SPOKEN_SCOPE
