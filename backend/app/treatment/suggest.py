"""Which symptom check a chat message is really asking for.

"My child has loose motions since two days, what to do?" is a request for
personal guidance, not for literature. Journal abstracts answer it badly;
the symptom check answers it properly — danger signs first, then safe doses
for that child's age. The chat offers the matching check when a message
names a complaint the check covers and is about someone's own situation or
asks what to do.
"""

from __future__ import annotations

import re

_COMPLAINTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "diarrhoea_vomiting",
        re.compile(
            r"\b(?:diarrh\w*|loose\s+(?:motions?|stools?)|motions|vomit\w*|throwing\s+up|"
            r"stomach\s+(?:upset|bug)|gastro\w*)\b",
            re.I,
        ),
    ),
    (
        "urinary",
        re.compile(
            r"\b(?:burning\s+(?:urine|urination|while\s+urinating|when\s+(?:i\s+)?pee)|"
            r"pain(?:ful)?\s+(?:urination|while\s+urinating|when\s+passing\s+urine)|"
            r"uti|urine\s+infection|urinary\s+(?:tract\s+)?infection)\b",
            re.I,
        ),
    ),
    (
        "cough_cold",
        re.compile(
            r"\b(?:cough\w*|cold|sore\s+throat|throat\s+pain|runny\s+nose|blocked\s+nose|"
            r"sneez\w*|flu|earache)\b",
            re.I,
        ),
    ),
    ("fever", re.compile(r"\b(?:fever\w*|high\s+temperature|temperature|bukhar|pyrexia)\b", re.I)),
    ("headache", re.compile(r"\b(?:headache\w*|migraine\w*|head\s+(?:pain|ache))\b", re.I)),
    (
        "rash_allergy",
        re.compile(r"\b(?:rash\w*|itch\w*|hives|allerg\w*|urticaria)\b", re.I),
    ),
    (
        "body_pain",
        re.compile(
            r"\b(?:back\s+pain|backache|body\s+(?:pain|ache)|joint\s+pain|muscle\s+pain|"
            r"sprain\w*|knee\s+pain|neck\s+pain)\b",
            re.I,
        ),
    ),
)
_PERSONAL = re.compile(
    r"\b(?:i|i'm|im|i've|my|me|mine|our|we|son|daughter|baby|child|kid|kids|wife|husband|"
    r"mother|father|mom|mum|dad|brother|sister|grandmother|grandfather|friend)\b",
    re.I,
)
_ASKS_WHAT_TO_DO = re.compile(
    r"\b(?:what\s+(?:to|should|can)\s+(?:i\s+)?(?:do|take|give)|"
    r"(?:what|which)\s+(?:medicine|tablet|drug|syrup)|"
    r"how\s+to\s+(?:treat|cure|stop|get\s+rid)|home\s+remed\w*|medicine\s+for|tablet\s+for)\b",
    re.I,
)


def suggest_complaint(message: str) -> str | None:
    """The symptom check to offer for ``message``, or None."""
    if not (_PERSONAL.search(message) or _ASKS_WHAT_TO_DO.search(message)):
        return None
    for complaint, pattern in _COMPLAINTS:
        if pattern.search(message):
            return complaint
    return None


def guidance_answer(complaint: str) -> str:
    """What to say about a personal complaint without a language model: the
    danger signs and the home care from the symptom check's own protocol
    (NHS, NICE and WHO guidance), with the sources linked — rather than
    quoting research abstracts at someone who asked what to do."""
    from app.treatment.engine import assess, get_protocol
    from app.treatment.formulary import Profile
    from app.treatment.sources import SOURCES

    protocol = get_protocol(complaint)
    danger = protocol.questions[0]
    emergencies = [o.label for o in danger.options if o.urgency == "emergency"]
    urgent = [o.label for o in danger.options if o.urgency == "urgent"]
    typical = assess(protocol, Profile(age_years=30), {q.id: ["none"] for q in protocol.questions})
    lines = [
        f"The best next step is the **{protocol.name} check** below: it asks about warning "
        "signs first, then tells you what to do and which medicines are safe, with the dose "
        "for the person's age.",
        "",
        "**Get emergency help now (call 112 or 108 in India) if any of these apply:**",
        *[f"- {label}" for label in emergencies],
    ]
    if urgent:
        lines += ["", "**See a doctor today if:**", *[f"- {label}" for label in urgent]]
    if typical.self_care:
        lines += ["", "**Meanwhile, at home:**", *[f"- {item}" for item in typical.self_care]]
    links = [
        f"[{SOURCES[k].publisher}: {SOURCES[k].title}]({SOURCES[k].url})"
        for k in protocol.source_keys
        if k in SOURCES
    ]
    if links:
        lines += ["", "Sources: " + " · ".join(links)]
    return "\n".join(lines)
