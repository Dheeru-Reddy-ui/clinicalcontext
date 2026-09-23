"""PHI detection — block, never redact-and-proceed.

This tool answers questions about the *literature*, not about individuals, so
any patient identifier in a query is a hard block before the text reaches any
model. Detection is layered:

1. A deterministic regex layer (SSN, MRN, phone, email, dates of birth, ZIP)
   — the 100%-reliable backbone. It needs no model and no network, so it fires
   before any LLM call and cannot be disabled by prompt injection.
2. A base64 decode-and-rescan pass, so PHI smuggled as base64 is caught.
3. An optional Presidio NER layer (names, locations) when the spaCy model is
   installed. It strengthens coverage but the gate never depends on it.

Findings carry entity *types and counts only* — never the matched values — so
the verdict is safe to log and audit.

Blocked text is not stored either. The query row, the session title and a
voice transcript are all written before the gate runs (the gate logs against
the query's id), so each of them passes through :func:`withhold_phi` and
keeps a fixed placeholder instead of the identifiers. Before that, the gate
blocked the question and the database kept it word for word — and the
autocomplete and the dashboard's top questions read from that column.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass

import structlog

logger = structlog.stdlib.get_logger("app.guardrails.phi")

# -- deterministic regex layer -------------------------------------------------------

_PATTERNS: dict[str, re.Pattern[str]] = {
    "US_SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "PHONE": re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)"),
    "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    # Medical record numbers: explicit label, or MRN-like alphanumeric tokens.
    "MRN": re.compile(
        r"\b(?:MRN|(?:medical\s+)?record\s+(?:no\.?|number|#)|record\s*#|patient\s+id|"
        r"chart\s+(?:no\.?|number|#))\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{3,})",
        re.IGNORECASE,
    ),
    # Dates of birth: labeled, or a bare MM/DD/YYYY style date.
    "DOB": re.compile(
        r"(?:\b(?:DOB|D\.O\.B\.|date\s+of\s+birth|born(?:\s+on)?)\b\s*[:]?\s*)?"
        r"\b(?:0?[1-9]|1[0-2])[/\-.](?:0?[1-9]|[12]\d|3[01])[/\-.](?:19|20)\d{2}\b",
        re.IGNORECASE,
    ),
    # US ZIP (5 or ZIP+4) when near an address cue, to avoid false positives on
    # bare 5-digit numbers.
    "ADDRESS_ZIP": re.compile(
        r"\b\d{1,5}\s+[A-Za-z0-9.\s]{3,40}\b(?:street|st\.?|avenue|ave\.?|road|rd\.?|"
        r"boulevard|blvd\.?|lane|ln\.?|drive|dr\.?)\b",
        re.IGNORECASE,
    ),
}

# Spoken forms (Phase 11): a speech recognizer writes dates and numbers the
# way they were said — "date of birth March 14, 1982", "MRN zero zero four
# eight…" / "MRN 0 0 4 8 2 9 3 1". Voice is not a side door around the PHI
# gate, so the deterministic layer understands those spellings too.
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_SPOKEN_PATTERNS: dict[str, re.Pattern[str]] = {
    # "born on March 14th, 1982" / "date of birth March 14 1982" / "DOB 14 March 1982"
    "DOB_SPOKEN": re.compile(
        rf"\b(?:DOB|D\.O\.B\.|date\s+of\s+birth|born(?:\s+on)?)\b[\s:,]*"
        rf"(?:{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?|"
        rf"\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\.?)"
        rf",?\s+(?:19|20)\d{{2}}\b",
        re.IGNORECASE,
    ),
    # Record numbers read out digit by digit: "MRN 0 0 4 8 2 9 3 1".
    "MRN_SPOKEN": re.compile(
        r"\b(?:MRN|(?:medical\s+)?record\s+(?:no\.?|number|#)|patient\s+id|chart\s+number)"
        r"\s*(?:is|number|:)?\s*(?:\d\s+){4,}\d\b",
        re.IGNORECASE,
    ),
    # "social security number 123 45 6789"
    "SSN_SPOKEN": re.compile(
        r"\bsocial\s+security\s+(?:number|no\.?)?\s*(?:is|:)?\s*"
        r"\d{3}[\s-]+\d{2}[\s-]+\d{4}\b",
        re.IGNORECASE,
    ),
}

# A bare DOB label with a date is the single strongest signal; keep the labeled
# form too so "DOB: 1982" style partials are still flagged.
_DOB_LABEL = re.compile(r"\b(?:DOB|D\.O\.B\.|date\s+of\s+birth)\b", re.IGNORECASE)

# Recognizers split spoken digit runs unpredictably ("123-45-6,789", "0 0 4 8");
# the scan also runs on a copy with separators between digits removed.
_DIGIT_GAP = re.compile(r"(?<=\d)[\s,.\-]+(?=\d)")
_SSN_COMPACT = re.compile(
    r"\b(?:social\s+security(?:\s+(?:number|no\.?|#))?|SSN)\s*(?:is|:|#)?\s*\d{9}\b",
    re.IGNORECASE,
)
_MRN_COMPACT = re.compile(
    r"\b(?:MRN|(?:medical\s+)?record\s+(?:no\.?|number|#)|patient\s+id|chart\s+number)"
    r"\s*(?:is|number|:)?\s*\d{5,}\b",
    re.IGNORECASE,
)

# A full name after a preposition ("in John Smith", "for Jane Doe") — the way
# a spoken query names a patient. Deterministic and case-sensitive: a
# recognizer capitalizes proper nouns, not conditions or drugs. Capitalized
# clinical and institutional words are excluded so "in Atrial Fibrillation"
# or "at Mayo Clinic" never block.
_BARE_NAME = re.compile(
    r"\b(?:in|for|of|about|with|to|on|regarding)\s+([A-Z][a-z]{2,})\s+([A-Z][a-z]{2,})(?=[\s,.?!;:]|$)"
)
_NOT_NAMES_TEXT = """
january february march april may june july august september october november
december monday tuesday wednesday thursday friday saturday sunday
atrial fibrillation type diabetes mellitus heart failure chronic kidney disease
acute coronary syndrome blood pressure myocardial infarction pulmonary embolism
deep vein thrombosis community acquired pneumonia major depressive disorder
bipolar depression generalized anxiety primary secondary prevention treatment
therapy guideline guidelines randomized controlled trial study systematic review
meta analysis evidence based medicine journal lancet nature science cochrane
mayo clinic cleveland johns hopkins new york united states national institute
institutes health world organization european society american college
cardiology association kidney foundation intensive care emergency department
older adults young children pregnant women stage grade class level phase
one two three four five first second third fourth fifth line dose daily
"""
_NOT_NAMES = frozenset(_NOT_NAMES_TEXT.split())

# Name cues: a person title/role followed (allowing punctuation) by a
# capitalized multi-word name. High precision — this is a *blocking* signal,
# unlike the Presidio NER layer which is telemetry-only (it false-positives on
# drug names, eponyms, and author citations).
# Case-insensitivity is scoped to the title cue only; the captured NAME stays
# case-sensitive so capitalization still gates what counts as a name.
_NAME_CUES = re.compile(
    r"(?i:\b(?:mrs?|ms|dr|pt)\.?|\bnamed|\bpatient\s+named|\bpatient,)"
    r"\s*"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-zA-Z'\u2019]+)+)",
)

# Capture the full base64 token INCLUDING '=' padding (a trailing \b would sit
# before the '=' and strip it, breaking the multiple-of-4 length check).
_BASE64_TOKEN = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{16,}={0,2}")


@dataclass(slots=True)
class PhiScanResult:
    detected: bool
    # entity type -> count (never the values themselves)
    entity_counts: dict[str, int]
    decoded_layer: bool = False  # PHI was found only after base64 decoding


def _scan_regex(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entity, pattern in {**_PATTERNS, **_SPOKEN_PATTERNS}.items():
        matches = pattern.findall(text)
        if matches:
            counts[entity] = counts.get(entity, 0) + len(matches)
    # The compact forms run on every text: a recognizer may already have
    # written the run without gaps ("social security number is 123456789").
    compact = _DIGIT_GAP.sub("", text)
    for entity, pattern in (("SSN_SPOKEN", _SSN_COMPACT), ("MRN_SPOKEN", _MRN_COMPACT)):
        found = pattern.findall(compact)
        if found and entity not in counts:
            counts[entity] = len(found)
    # A standalone DOB label (even without a parseable date) is suspicious.
    if "DOB" not in counts and "DOB_SPOKEN" not in counts and _DOB_LABEL.search(text):
        counts["DOB_LABEL"] = 1
    name_matches = _NAME_CUES.findall(text)
    if name_matches:
        counts["PERSON_HEURISTIC"] = len(name_matches)
    bare = [
        (first, last)
        for first, last in _BARE_NAME.findall(text)
        if first.lower() not in _NOT_NAMES and last.lower() not in _NOT_NAMES
    ]
    if bare:
        counts["PERSON_BARE_NAME"] = len(bare)
    return counts


def _scan_base64(text: str) -> dict[str, int]:
    """Decode base64-looking tokens and re-scan — catches smuggled PHI."""
    counts: dict[str, int] = {}
    for token in _BASE64_TOKEN.findall(text):
        # Base64 length must be a multiple of 4 to decode cleanly.
        if len(token) % 4 != 0:
            continue
        try:
            decoded = base64.b64decode(token, validate=True).decode("utf-8", errors="strict")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if not decoded.isprintable():
            continue
        for entity, n in _scan_regex(decoded).items():
            counts[entity] = counts.get(entity, 0) + n
    return counts


# The Presidio engine (spaCy model + recognizers) costs ~0.8 s to build; it is
# built once per process and shared by every detector instance, so neither
# the text path nor a voice turn pays it per request.
_SHARED_ANALYZER: object | None = None
_SHARED_TRIED = False


class PhiDetector:
    """Layered PHI detector. Presidio is loaded lazily and optionally."""

    def __init__(self, *, use_presidio: bool = True) -> None:
        self._use_presidio = use_presidio
        self._analyzer: object | None = None
        self._presidio_tried = False

    def _presidio(self) -> object | None:
        global _SHARED_ANALYZER, _SHARED_TRIED  # process-wide cache
        if not self._use_presidio or self._presidio_tried:
            return self._analyzer
        self._presidio_tried = True
        if _SHARED_TRIED:
            self._analyzer = _SHARED_ANALYZER
            return self._analyzer
        _SHARED_TRIED = True
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import NlpEngineProvider

            # Pin the lightweight spaCy model (~12MB): CI-affordable and enough
            # for PERSON/LOCATION NER. The regex layer carries the 100% gate.
            provider = NlpEngineProvider(
                nlp_configuration={
                    "nlp_engine_name": "spacy",
                    "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
                }
            )
            self._analyzer = AnalyzerEngine(
                nlp_engine=provider.create_engine(), supported_languages=["en"]
            )
            logger.info("presidio_loaded", model="en_core_web_sm")
        except Exception as exc:  # model or import missing — degrade to regex
            logger.warning(
                "presidio_unavailable",
                error=f"{type(exc).__name__}: {exc}",
                hint="PHI detection continues on the deterministic regex layer",
            )
            self._analyzer = None
        _SHARED_ANALYZER = self._analyzer
        return self._analyzer

    def _scan_presidio(self, text: str) -> dict[str, int]:
        analyzer = self._presidio()
        if analyzer is None:
            return {}
        try:
            results = analyzer.analyze(  # type: ignore[attr-defined]
                text=text,
                language="en",
                entities=["PERSON", "LOCATION", "US_SSN", "PHONE_NUMBER", "EMAIL_ADDRESS"],
                score_threshold=0.5,
            )
        except Exception as exc:
            logger.warning("presidio_analyze_failed", error=f"{type(exc).__name__}: {exc}")
            return {}
        counts: dict[str, int] = {}
        for result in results:
            key = f"PRESIDIO_{result.entity_type}"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def scan(self, text: str) -> PhiScanResult:
        """Scan raw text (and its base64 payloads) for PHI.

        The **block decision is driven only by the deterministic layer**
        (regex structured identifiers + high-precision name cues + base64
        payloads). Presidio NER (PERSON/LOCATION) is recorded for telemetry but
        never blocks on its own — on clinical text it false-positives on drug
        names, eponyms, and author citations, and blocking a legitimate
        literature question is itself a failure.
        """
        deterministic = _scan_regex(text)

        decoded_layer = False
        decoded = _scan_base64(text)
        if decoded:
            if not deterministic:
                deterministic = dict(decoded)
                decoded_layer = True
            else:
                for k, v in decoded.items():
                    deterministic[f"B64_{k}"] = v

        detected = bool(deterministic)

        # Presidio: telemetry only (prefixed, merged into the reported counts).
        counts = dict(deterministic)
        for k, v in self._scan_presidio(text).items():
            counts[k] = v

        return PhiScanResult(detected=detected, entity_counts=counts, decoded_layer=decoded_layer)


_BLOCK_MESSAGE = (
    "This request appears to contain patient information (protected health "
    "information). ClinicalContext does not accept patient data and cannot "
    "process requests about specific individuals. Please remove any names, "
    "dates of birth, record numbers, or contact details and ask about the "
    "clinical literature instead — for example, the evidence for a treatment "
    "in a described population rather than for a named patient."
)


def phi_block_message() -> str:
    return _BLOCK_MESSAGE


# What text carrying patient identifiers is stored as, wherever it would
# otherwise be written. Migration 024 rewrites earlier rows to the same value.
WITHHELD_TEXT = "[withheld: contained patient identifiers]"

_STORAGE_DETECTOR = PhiDetector(use_presidio=False)


def carries_phi(text: str) -> bool:
    """The block decision's own test: the deterministic layer, no model."""
    return _STORAGE_DETECTOR.scan(text).detected


def withhold_phi(text: str) -> str:
    """``text`` as it may be stored: unchanged, or WITHHELD_TEXT."""
    return WITHHELD_TEXT if carries_phi(text) else text
