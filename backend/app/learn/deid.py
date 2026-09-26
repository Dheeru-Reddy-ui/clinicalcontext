"""De-identification for the clinical note summarizer.

Everywhere else ClinicalContext refuses patient identifiers outright
(app/guardrails/phi.py): Ask and Chat answer questions about the literature,
so a name has no business in them. A note summarizer is the one tool whose
input is a patient's record by nature — so instead of refusing, it removes.
Every identifier found is replaced with a typed placeholder ("[NAME]",
"[DATE]", "[RECORD NUMBER]" ...) before the text goes anywhere: before a model
reads it, before anything is logged. Nothing is stored, and the page shows the
person exactly what was sent.

Detection runs in two layers:

1. Deterministic rules — labelled fields ("Name:", "UHID", "IP No",
   "Address:"), titles before a name ("Mr.", "Smt.", "S/O"), phone numbers in
   Indian and international forms, emails, Aadhaar-shaped numbers, web links
   and exact dates. Ages, durations and times of day are kept: the summary
   needs them and they do not identify anyone on their own.
2. When the spaCy model is installed, the Presidio name recognizer (the same
   one the PHI gate uses), for names that carry no label — with medical
   eponyms ("Graves' disease", "Crohn's") left alone.

It cannot be perfect, and the page says so.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

import structlog

from app.guardrails.phi import PhiDetector

logger = structlog.stdlib.get_logger("app.learn.deid")

# Where a labelled value stops: a wide gap, or another field's label.
_VALUE_END = (
    r"(?=[ \t]{2,}|\t|[ \t]+(?:age|sex|gender|uhid|mrn|ip[ \t]*no|op[ \t]*no|dob|date|"
    r"ward|bed|phone|mobile|contact|reg(?:istration)?[ \t]*no)\b|$)"
)

_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DAY = r"(?:0?[1-9]|[12]\d|3[01])"
_MONTH_NUM = r"(?:0?[1-9]|1[0-2])"
_YEAR = r"(?:19|20)\d{2}"

# (placeholder, pattern). A pattern with a group named "value" replaces only
# that group, keeping the label ("Name: [NAME]"); otherwise the whole match.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "NAME",
        re.compile(
            r"(?im)^(?P<label>[ \t]*(?:patient(?:'s)?[ \t]+name|name(?:[ \t]+of[ \t]+(?:the[ \t]+)?"
            r"patient)?|pt\.?[ \t]+name|attendant|guardian|informant|father'?s?[ \t]+name|"
            r"mother'?s?[ \t]+name|husband'?s?[ \t]+name|spouse|next[ \t]+of[ \t]+kin|consultant|"
            r"treating[ \t]+doctor|doctor|physician|surgeon|referred[ \t]+by|ref\.?[ \t]+by|"
            r"signed[ \t]+by|reported[ \t]+by)[ \t]*[:\-][ \t]*)(?P<value>\S[^\n]*?)" + _VALUE_END
        ),
    ),
    (
        "ADDRESS",
        re.compile(
            r"(?im)^(?P<label>[ \t]*(?:address|addr\.?|residence|resident[ \t]+of|"
            r"permanent[ \t]+address|correspondence[ \t]+address)[ \t]*[:\-][ \t]*)"
            r"(?P<value>\S[^\n]*)$"
        ),
    ),
    # Aadhaar-shaped: 12 digits in groups of four.
    ("ID NUMBER", re.compile(r"(?<!\d)\d{4}[ \-]\d{4}[ \-]\d{4}(?!\d)")),
    (
        "RECORD NUMBER",
        re.compile(
            r"(?i)\b(?P<label>(?:mrn|uhid|cr[ \t]*no|ip[ \t]*(?:no|number)|op[ \t]*(?:no|number)|"
            r"reg(?:istration)?[ \t]*(?:no|number)|hospital[ \t]*(?:no|number|id)|patient[ \t]*id|"
            r"case[ \t]*(?:no|number)|bed[ \t]*(?:no|number)|ward[ \t]*(?:no|number)|"
            r"abha(?:[ \t]*(?:no|number|id))?|aadhaa?r(?:[ \t]*(?:no|number))?|"
            r"sample[ \t]*(?:no|id)|lab[ \t]*(?:no|id)|accession[ \t]*(?:no|number)|"
            r"policy[ \t]*(?:no|number)|record[ \t]*(?:no|number))\.?[ \t]*[:#\-]?[ \t]*)"
            r"(?P<value>[A-Za-z0-9][A-Za-z0-9/\-]{2,})"
        ),
    ),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("LINK", re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.I)),
    (
        "PHONE",
        re.compile(
            # Indian mobiles (+91 98xxx xxxxx), STD landlines (040-2345 6789),
            # and the international 3-3-4 form.
            r"(?<![\d.])(?:\+?91[ \-]?)?[6-9]\d{4}[ \-]?\d{5}(?![\d.])|"
            r"(?<![\d.])0\d{2,4}[ \-]\d{3,4}[ \-]?\d{4}(?![\d.])|"
            r"(?<![\d.])(?:\+?\d{1,3}[ .\-])?\(?\d{3}\)?[ .\-]\d{3}[ .\-]\d{4}(?![\d.])"
        ),
    ),
    (
        "PIN CODE",
        re.compile(r"(?i)\b(?P<label>pin(?:[ \t]*code)?[ \t]*[:\-]?[ \t]*)(?P<value>\d{6})\b"),
    ),
    (
        "DATE",
        re.compile(
            rf"\b{_DAY}[/\-.]{_MONTH_NUM}[/\-.](?:{_YEAR}|\d{{2}})\b|"
            rf"\b{_YEAR}[/\-.]{_MONTH_NUM}[/\-.]{_DAY}\b|"
            rf"\b{_DAY}(?:st|nd|rd|th)?[ \t]+(?:of[ \t]+)?{_MONTH}\.?,?[ \t]+{_YEAR}\b|"
            rf"\b{_MONTH}\.?[ \t]+{_DAY}(?:st|nd|rd|th)?,?[ \t]+{_YEAR}\b",
            re.I,
        ),
    ),
    (
        "NAME",
        re.compile(
            # A title, then a capitalized name of up to four words.
            r"\b(?:Mr|Mrs|Ms|Miss|Master|Baby|Dr|Smt|Shri|Sri|Kumari|Kum|B/O|S/O|D/O|W/O|C/O)\.?"
            r"[ \t]+(?P<value>[A-Z][A-Za-z'.]+(?:[ \t]+[A-Z][A-Za-z'.]+){0,3})"
        ),
    ),
)

# A name recognized by the model is left alone when it is part of an eponym.
_EPONYM_AFTER = re.compile(
    r"^(?:['\N{RIGHT SINGLE QUOTATION MARK}]s?)?\s+"
    r"(?:disease|syndrome|sign|signs|test|score|criteria|reflex|palsy|"
    r"phenomenon|triad|classification|procedure|operation|manoeuvre|maneuver|fracture|"
    r"tumou?r|lymphoma|sarcoma|ulcer|node|nodes|hernia|cyst|duct|position|method|scale|"
    r"staging|encephalopathy|thyroiditis|disorder|anomaly|formula|equation|lesion|space)\b",
    re.I,
)
_PLACEHOLDER = re.compile(r"\[[A-Z ]+\]")
# A name the model finds without a title must look like a full name — two to
# four capitalized words — because one capitalized word in a clinical note is
# far more often a drug, a dosage form or an eponym ("Tab", "Aspirin",
# "Graves") than a person. Single names after a title are the rules' job.
_NAME_SHAPE = re.compile(r"[A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-]+){1,3}")
_NEVER_NAMES_TEXT = """
tab tabs tablet cap caps capsule inj injection syp syr syrup susp oint drops neb
gel cream lotion sachet patient doctor hospital ward unit clinic diagnosis
review advice discharge admission history examination investigation
investigations plan impression summary report medications medicine treatment
follow day night
"""
_NEVER_NAMES = frozenset(_NEVER_NAMES_TEXT.split())
_PERSON_MIN_SCORE = 0.85

_RECOGNIZER = PhiDetector(use_presidio=True)


@dataclass(slots=True)
class Redaction:
    """The text as it may be sent on, and what was taken out of it (counts by
    placeholder — never the values)."""

    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _replace(label: str, counts: dict[str, int]) -> Callable[[re.Match[str]], str]:
    def sub(match: re.Match[str]) -> str:
        has_value = "value" in match.re.groupindex and match.group("value") is not None
        taken = match.group("value") if has_value else match.group(0)
        if _PLACEHOLDER.fullmatch(taken.strip()):
            return match.group(0)  # already replaced: never counted twice
        counts[label] = counts.get(label, 0) + 1
        if has_value:
            start = match.start("value") - match.start()
            end = match.end("value") - match.start()
            whole = match.group(0)
            return f"{whole[:start]}[{label}]{whole[end:]}"
        return f"[{label}]"

    return sub


def _names_by_model(text: str) -> list[tuple[int, int]]:
    """Spans the Presidio recognizer is sure are people, eponyms excluded."""
    analyzer = _RECOGNIZER._presidio()  # the process-wide shared analyzer
    if analyzer is None:
        return []
    try:
        results = analyzer.analyze(  # type: ignore[attr-defined]
            text=text, language="en", entities=["PERSON"], score_threshold=_PERSON_MIN_SCORE
        )
    except Exception as exc:  # the rules above already ran; the model is a bonus
        logger.warning("deid_name_model_failed", error=type(exc).__name__)
        return []
    spans: list[tuple[int, int]] = []
    for result in results:
        start, end = int(result.start), int(result.end)
        name = text[start:end]
        if not _NAME_SHAPE.fullmatch(name) or _PLACEHOLDER.search(name):
            continue
        if any(word.lower().strip("'-") in _NEVER_NAMES for word in name.split()):
            continue
        if _EPONYM_AFTER.match(text[end : end + 40]):
            continue
        spans.append((start, end))
    return spans


def redact(text: str) -> Redaction:
    """``text`` with every identifier found replaced by a typed placeholder."""
    counts: dict[str, int] = {}
    redacted = text
    for label, pattern in _RULES:
        redacted = pattern.sub(_replace(label, counts), redacted)
    spans = _names_by_model(redacted)
    for start, end in sorted(spans, reverse=True):
        redacted = f"{redacted[:start]}[NAME]{redacted[end:]}"
    if spans:
        counts["NAME"] = counts.get("NAME", 0) + len(spans)
    return Redaction(text=redacted, counts=counts)
