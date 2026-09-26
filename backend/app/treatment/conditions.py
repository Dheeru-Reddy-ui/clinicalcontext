"""Conditions a person types in their own words.

The symptom check's medicine rules are keyed to a short list of long-term
conditions (formulary.Condition). People describe their health in many more
ways — "CKD", "on dialysis", "sugar", "dengue last week", "TB" — so what
they type is read here:

- a phrase that names a listed condition counts as ticking it ("CKD" is
  kidney disease), so every rule written for that condition applies;
- a few illnesses change which medicines are safe on their own: dengue
  (no ibuprofen — bleeding risk) and chickenpox (no ibuprofen — serious
  skin infections);
- anything else is kept as typed and reported back as not covered by the
  checks, with advice to ask a pharmacist before taking a medicine. Nothing
  a person tells us is dropped silently.

Matching is deliberately conservative in one direction only: a phrase that
might mean a listed condition is read as that condition (more caution), and
a phrase is never read as the absence of one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app.treatment.formulary import Condition

Flag = Literal["dengue", "chickenpox", "pregnancy"]

# Word-bounded, case-insensitive. Kidney stones and mouth or leg ulcers are
# deliberately not here: they are not what the kidney and stomach-ulcer rules
# guard against.
_CONDITION_PATTERNS: tuple[tuple[Condition, str], ...] = (
    ("asthma", r"\basthma|\breactive airways?\b"),
    (
        "kidney_disease",
        r"\b(?:kidney|renal)\s+(?:disease|failure|impairment|problem|damage|insufficiency|"
        r"transplant)|\bckd\b|\besrd\b|\bdialysis\b|\bnephr(?:itis|otic|opathy)\b|"
        r"\bweak kidneys?\b",
    ),
    (
        "liver_disease",
        r"\bliver\s+(?:disease|failure|damage|problem|cirrhosis|transplant)|\bcirrhosis\b|"
        r"\bhepatitis\b|\bhep\s?[abc]\b|\bfatty\s+liver\b|\bnafld\b|\bmasld\b|\bjaundice\b",
    ),
    (
        "stomach_ulcer",
        r"\b(?:stomach|gastric|peptic|duodenal)\s+ulcers?\b|\bulcers?\s+in\s+(?:the\s+|my\s+)?"
        r"stomach\b|\bpud\b|\b(?:gi|gastrointestinal|stomach|gut)\s+bleed(?:ing)?\b|"
        r"\bh\.?\s?pylori\b|\bha?ematemesis\b|\bmela?ena\b",
    ),
    (
        "heart_disease",
        r"\bheart\s+(?:failure|disease|attack|problem|condition|block)|\bcardiac\b|"
        r"\bcoronary\b|\bangina\b|\b(?:cad|chf|ihd)\b|\bmyocardial\b|\bstents?\b|"
        r"\bbypass\s+surgery\b|\bcabg\b|\bcardiomyopathy\b|\barrhythmia\b|"
        r"\batrial\s+fibrillation\b|\bafib\b|\bvalve\s+(?:disease|replacement)\b",
    ),
    (
        "diabetes",
        # "Sugar" alone is how many people in India say diabetes; "low
        # sugar" is not.
        r"\bdiabet|\bhigh\s+(?:blood\s+)?sugar\b|(?<!low )\bsugar\b|\bt[12]dm\b|"
        r"\bdm\s*(?:type\s*)?[12]?\b",
    ),
    (
        "weak_immunity",
        r"\bhiv\b|\baids\b|\bimmuno(?:compromised|suppress\w*|deficien\w*)|\bchemo\w*|"
        r"\bcancer\b|\btransplant\b|\blong[-\s]term\s+steroids\b|\bweak\s+immun\w*|"
        r"\blow\s+immunity\b|\bneutropeni\w*|\bsplenectomy\b|\basplenia\b|\bno\s+spleen\b",
    ),
    (
        "bleeding_disorder",
        r"\bbleeding\s+disorder\b|\bha?emophilia\b|\bvon\s+willebrand\b|"
        r"\bthrombocytopeni\w*|\blow\s+platelets?\b|\bitp\b|\bclotting\s+disorder\b",
    ),
    (
        "high_blood_pressure",
        # "BP" alone usually means high blood pressure; "low BP" does not.
        r"\bhypertensi\w*|\bhigh\s+(?:blood\s+)?pressure\b|\bhtn\b|"
        r"(?<!low )\bbp\b(?!\s+(?:is\s+)?low)",
    ),
    (
        "lung_disease",
        r"\bcopd\b|\bemphysema\b|\bchronic\s+bronchitis\b|\bbronchiectasis\b|"
        r"\blung\s+(?:disease|fibrosis|condition|problem)|\bpulmonary\s+fibrosis\b|\bild\b|"
        r"\binterstitial\s+lung\b|\btb\b|\btuberculosis\b|\bcystic\s+fibrosis\b",
    ),
)
_CONDITIONS: tuple[tuple[Condition, re.Pattern[str]], ...] = tuple(
    (condition, re.compile(pattern, re.I)) for condition, pattern in _CONDITION_PATTERNS
)

_FLAGS: tuple[tuple[Flag, re.Pattern[str]], ...] = (
    ("dengue", re.compile(r"\bdengue\b", re.I)),
    ("chickenpox", re.compile(r"\bchicken\s?pox\b|\bvaricella\b", re.I)),
    ("pregnancy", re.compile(r"\bpregnan\w*", re.I)),
)

# How each flag is described back to the person.
FLAG_LABELS: dict[Flag, str] = {
    "dengue": "dengue",
    "chickenpox": "chickenpox",
    "pregnancy": "pregnancy",
}


@dataclass(frozen=True, slots=True)
class ConditionReading:
    """How one typed phrase was read."""

    text: str
    conditions: tuple[Condition, ...] = ()
    flags: tuple[Flag, ...] = ()

    @property
    def understood(self) -> bool:
        return bool(self.conditions or self.flags)


def read_condition(text: str) -> ConditionReading:
    phrase = " ".join(text.split())
    return ConditionReading(
        text=phrase,
        conditions=tuple(c for c, pattern in _CONDITIONS if pattern.search(phrase)),
        flags=tuple(f for f, pattern in _FLAGS if pattern.search(phrase)),
    )


def read_conditions(texts: Iterable[str]) -> list[ConditionReading]:
    """Each distinct phrase, read; blanks and repeats dropped."""
    seen: set[str] = set()
    readings: list[ConditionReading] = []
    for text in texts:
        reading = read_condition(text)
        key = reading.text.lower()
        if reading.text and key not in seen:
            seen.add(key)
            readings.append(reading)
    return readings
