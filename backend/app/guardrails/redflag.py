"""Red-flag emergency detector.

Cheap, deterministic pattern matching for active-emergency indicators. It runs
for *every* query — including legitimate clinician ones — because prepending an
escalation banner costs nothing and is the right default when words like
"anaphylaxis" or "suicidal" appear. It does not block: the banner is shown
before any retrieved content, and the literature answer still follows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# category -> patterns. Kept deliberately broad; false positives here are cheap.
_RED_FLAGS: dict[str, tuple[re.Pattern[str], ...]] = {
    "cardiac": (
        re.compile(
            r"\bchest\s+pain\b.*\b(?:radiat|jaw|left\s+arm|short(?:ness)?\s+of\s+breath)", re.I
        ),
        re.compile(r"\b(?:crushing|severe)\s+chest\s+(?:pain|pressure)\b", re.I),
        re.compile(r"\b(?:active|acute)\s+(?:mi|myocardial\s+infarction|heart\s+attack)\b", re.I),
        re.compile(r"\bstemi\b", re.I),
    ),
    "anaphylaxis": (
        re.compile(r"\banaphylaxis\b|\banaphylactic\b", re.I),
        re.compile(
            r"\b(?:throat|tongue)\s+(?:swelling|closing)\b.*\b(?:breath|hives|allerg)", re.I
        ),
    ),
    "stroke": (
        re.compile(r"\b(?:stroke|cva)\b.*\b(?:symptom|sudden|acute)\b", re.I),
        re.compile(r"\b(?:facial\s+droop|slurred\s+speech|sudden\s+weakness)\b", re.I),
        re.compile(r"\bf\.?a\.?s\.?t\.?\b.*\bstroke\b", re.I),
    ),
    "suicidal": (
        re.compile(r"\bsuicid(?:e|al)\b", re.I),
        re.compile(r"\b(?:kill|hurt|harm)\s+(?:myself|himself|herself|themsel)", re.I),
        re.compile(r"\b(?:end|take)\s+(?:my|their|his|her)\s+(?:own\s+)?life\b", re.I),
        re.compile(r"\bself[-\s]?harm\b", re.I),
    ),
    "sepsis": (
        re.compile(r"\bseptic\s+shock\b|\bsepsis\b.*\b(?:hypotension|lactate|shock)", re.I),
        re.compile(r"\b(?:qsofa|sirs)\b.*\b(?:positive|criteria|met)\b", re.I),
    ),
    "airway_breathing": (
        re.compile(
            r"\b(?:respiratory\s+(?:arrest|failure)|not\s+breathing|stopped\s+breathing)\b", re.I
        ),
        re.compile(
            r"\b(?:cardiac\s+arrest|no\s+pulse|unresponsive\s+and\s+not\s+breathing)\b", re.I
        ),
    ),
}

_BANNER = (
    "⚠ EMERGENCY INDICATORS DETECTED. If this describes a real person right now, "
    "this is a potential medical emergency — call your local emergency number "
    "(112 in India and the EU, 108 for an ambulance in India, 911 in the US) or go "
    "to the nearest emergency department immediately. For suicidal thoughts, "
    "contact a crisis line (Tele-MANAS 14416 in India, 988 in the US). The "
    "information below is reference only and is not a substitute for emergency care."
)


@dataclass(slots=True)
class RedFlagResult:
    triggered: bool
    categories: list[str] = field(default_factory=list)
    banner: str | None = None


def detect_red_flags(query: str) -> RedFlagResult:
    categories = [
        category
        for category, patterns in _RED_FLAGS.items()
        if any(p.search(query) for p in patterns)
    ]
    if not categories:
        return RedFlagResult(triggered=False)
    return RedFlagResult(triggered=True, categories=sorted(categories), banner=_BANNER)
