"""The clinical note summarizer: a long note or report as a short, checked
summary.

1. Identifiers out first (``deid.redact``): the rest of this module, and the
   model, only ever see the redacted text. Nothing is stored or logged.
2. The note is numbered line by line — a long line is split into sentences —
   so every point in the summary can name the line it came from.
3. With a language model configured, it writes the summary under fixed
   headings, each point ending with its line numbers (prompt note_summary).
   Every point is then checked against the lines it names, with the grounding
   verifier the rest of ClinicalContext uses — which also rejects a number
   the note does not contain — and a point that fails is dropped. If too much
   fails, or no model is configured or answering, the summary is built from
   the note's own lines instead: its diagnosis, findings, medicines and plan,
   found by their headings.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

import structlog

from app.guardrails.grounding import (
    LexicalGroundingVerifier,
    invented_numbers,
    is_clinical_claim,
    normalize_answer,
    split_sentences,
)
from app.learn.deid import redact
from app.llm.chat import ChatMessage, ChatModel, LLMUnavailable
from app.prompts.loader import load_prompt

logger = structlog.stdlib.get_logger("app.learn.notes")

MAX_NOTE_CHARS = 15_000
PROMPT = ("note_summary", 1)
HEADINGS = ("Summary", "Problems", "Key findings", "Medications", "Plan", "Watch for")
_MAX_LINES = 400
_LONG_LINE = 280
# More of the summary than this failing its check means the model's version
# is not trusted at all: the note's own lines are used instead.
_REJECT_FRACTION = 0.30

Support = Literal["supported", "partially_supported", "quoted"]


@dataclass(slots=True)
class SummaryPoint:
    text: str
    lines: list[int]
    support: Support


@dataclass(slots=True)
class SummarySection:
    heading: str
    points: list[SummaryPoint] = field(default_factory=list)


@dataclass(slots=True)
class NoteSummary:
    sections: list[SummarySection]
    lines: list[str]
    redactions: dict[str, int]
    mode: Literal["llm", "extractive"]
    model: str | None = None
    removed: int = 0
    notice: str | None = None


def note_lines(text: str) -> list[str]:
    """The note as numbered lines (index 0 is line 1): blank lines dropped,
    a long line split into its sentences."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        lines.extend(split_sentences(line) if len(line) > _LONG_LINE else [line])
    return lines[:_MAX_LINES]


# -- the model's summary, checked point by point ------------------------------------------

_HEADING = re.compile(
    r"^\s*(?:#{1,4}\s*)?\**\s*(summary|problems?|key\s+findings|findings|medications?|"
    r"medicines|plan|watch\s+for)\s*\**\s*(?::|\N{EM DASH}|\N{EN DASH}|-)?\s*\**\s*(.*)$",
    re.I,
)
_BULLET = re.compile(r"^\s*(?:[-*+\N{BULLET}]|\d+[.)])\s+")
_MARKER = re.compile(r"\[(\d+)\]")
_MARKERS_TEXT = re.compile(r"\s*(?:\[\d+\])+")
_FIGURE = re.compile(r"\d")
_CANONICAL = {
    "summary": "Summary",
    "problem": "Problems",
    "problems": "Problems",
    "key findings": "Key findings",
    "findings": "Key findings",
    "medication": "Medications",
    "medications": "Medications",
    "medicines": "Medications",
    "plan": "Plan",
    "watch for": "Watch for",
}


async def _checked(output: str, lines: Sequence[str]) -> tuple[list[SummarySection], int, int, int]:
    """(sections, points kept, points removed, share of text removed as
    permille) from the model's summary."""
    verifier = LexicalGroundingVerifier()
    sections: list[SummarySection] = []
    current: SummarySection | None = None
    kept = removed = 0
    kept_chars = removed_chars = 0
    for raw in output.splitlines():
        if not raw.strip():
            continue
        heading = _HEADING.match(raw)
        text = raw
        if heading:
            name = _CANONICAL.get(" ".join(heading.group(1).lower().split()), "Summary")
            current = next((s for s in sections if s.heading == name), None)
            if current is None:
                current = SummarySection(name)
                sections.append(current)
            text = heading.group(2)
            if not text.strip():
                continue
        if current is None:
            current = SummarySection("Summary")
            sections.append(current)
        point = normalize_answer(_BULLET.sub("", text)).replace("\n", " ").strip()
        if not point:
            continue
        cited = [int(m) for m in _MARKER.findall(point) if 1 <= int(m) <= len(lines)]
        bare = _MARKERS_TEXT.sub("", point).strip()
        if not cited:
            # A figure with no line to show for it is a claim, whatever its words.
            has_figure = bool(_FIGURE.search(bare))
            support: str = "no_citation" if is_clinical_claim(bare) or has_figure else "framing"
        else:
            cited_lines = [lines[m - 1] for m in cited]
            support = await verifier.verify(bare, cited_lines)
            if support != "unsupported" and invented_numbers(bare, cited_lines):
                support = "unsupported"  # a summary never passes on a made-up value
        if support in ("unsupported", "no_citation"):
            removed += 1
            removed_chars += len(bare)
            continue
        if support == "framing":
            continue  # a sentence with nothing to check and nothing to cite
        kept += 1
        kept_chars += len(bare)
        current.points.append(
            SummaryPoint(
                text=bare,
                lines=sorted(set(cited)),
                support="supported" if support == "supported" else "partially_supported",
            )
        )
    total = kept_chars + removed_chars
    permille = round(1000 * removed_chars / total) if total else 0
    return [s for s in sections if s.points], kept, removed, permille


# -- the note's own lines ---------------------------------------------------------------

_CUES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (heading, re.compile(pattern, re.I))
    for heading, pattern in (
        (
            "Summary",
            r"^(?:chief\s+complaints?|presenting\s+complaints?|c/o|complaints?|reason\s+for\s+"
            r"(?:admission|referral|visit)|history\s+of\s+present(?:ing)?\s+illness|hpi|"
            r"clinical\s+(?:history|details|summary|notes?)|indication|brief\s+history|history)",
        ),
        (
            "Problems",
            r"^(?:(?:final|provisional|discharge|working|primary|secondary|differential)\s+)?"
            r"diagnos[ie]s|^impression|^assessment|^problem\s+list|^conclusion|^opinion",
        ),
        (
            "Key findings",
            r"^(?:on\s+)?examination|^o/e|^general\s+examination|^systemic\s+examination|"
            r"^vitals?|^vital\s+signs|^investigations?|^lab(?:oratory)?(?:\s+(?:results|"
            r"investigations|findings|reports?))?|^labs|^results|^findings|^imaging|^radiology|"
            r"^ecg|^echo|^x-?ray|^ct\b|^mri\b|^usg\b|^ultrasound|^report|^observations?",
        ),
        (
            "Medications",
            r"^(?:discharge\s+)?medications?|^medicines?|^drugs?|^rx\b|^treatment\s+(?:given|"
            r"advised|on\s+discharge)|^prescription|^current\s+medications?",
        ),
        (
            "Plan",
            r"^plan|^advice|^advised|^follow[\s-]?up|^recommendations?|^disposition|"
            r"^instructions|^management|^course\s+in\s+(?:the\s+)?hospital|^hospital\s+course",
        ),
        (
            "Watch for",
            r"^pending|^to\s+follow|^red\s+flags?|^warning\s+signs|^when\s+to\s+return|"
            r"^return\s+if|^review\s+if",
        ),
    )
)
_HEADER_LINE = re.compile(
    r"^([A-Za-z][A-Za-z /&()'-]{1,48}?)\s*(?::|\N{EM DASH}|\N{EN DASH}|-)\s*(.*)$"
)
_CAPS = {
    "Summary": 3,
    "Problems": 6,
    "Key findings": 10,
    "Medications": 14,
    "Plan": 8,
    "Watch for": 6,
}
# With no headings to go by: what each kind of line looks like.
_LOOKS_LIKE: dict[str, re.Pattern[str]] = {
    "Problems": re.compile(
        r"\b(?:diagnos\w*|impression|suggestive\s+of|consistent\s+with|known\s+case\s+of|"
        r"k/c/o|likely|probable)\b",
        re.I,
    ),
    "Key findings": re.compile(
        r"\d+(?:\.\d+)?\s*(?:mg/dl|g/dl|mmol/l|mmhg|%|/min|bpm|iu/l|u/l|ng/ml|pg/ml|"
        r"cells/\S+|/cumm|x\s*10|mm|cm)\b",
        re.I,
    ),
    "Medications": re.compile(
        r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|iu|units?)\b.*\b(?:od|bd|tds|qid|hs|sos|stat|"
        r"daily|once|twice|prn|q\d+h)\b",
        re.I,
    ),
    "Plan": re.compile(
        r"\b(?:advised|advice|follow[\s-]?up|review|continue|start|stop|refer\w*|plan|"
        r"repeat|recheck)\b",
        re.I,
    ),
}


_PLACEHOLDERS = re.compile(r"\[[A-Z ]+\]")
_FIELD_LABEL = re.compile(r"[A-Za-z][A-Za-z /&()'.-]{0,40}:")


def only_identifiers(line: str) -> bool:
    """A line that is nothing but field labels and removed identifiers
    ("Phone: [PHONE]", "Date of admission: [DATE] Date of discharge: [DATE]")
    has nothing clinical to put in a summary."""
    rest = _FIELD_LABEL.sub(" ", _PLACEHOLDERS.sub(" ", line))
    return bool(_PLACEHOLDERS.search(line)) and not re.search(r"[A-Za-z0-9]", rest)


def _section_for(header: str) -> str | None:
    for heading, pattern in _CUES:
        if pattern.search(header.strip()):
            return heading
    return None


def extractive_summary(lines: Sequence[str]) -> list[SummarySection]:
    """The summary made of the note's own lines, found by their headings (or,
    with no headings, by what each line looks like)."""
    sections: dict[str, SummarySection] = {h: SummarySection(h) for h in HEADINGS}
    current: str | None = None
    found_heading = False

    def add(heading: str, number: int, text: str) -> None:
        section = sections[heading]
        if len(section.points) < _CAPS[heading] and len(text) > 2:
            section.points.append(SummaryPoint(text=text, lines=[number], support="quoted"))

    for number, line in enumerate(lines, start=1):
        if only_identifiers(line):
            continue
        header = _HEADER_LINE.match(line)
        heading = _section_for(header.group(1)) if header else _section_for(line)
        if heading is not None and (header or len(line.split()) <= 5):
            found_heading = True
            current = heading
            rest = header.group(2).strip() if header else ""
            if rest:
                add(heading, number, rest)
            continue
        if (
            header
            and len(header.group(1).split()) <= 4
            and _section_for(header.group(1)) is None
            and current in (None, "Summary")
        ):
            continue  # another field ("Age/Sex:", "Ward:"), part of no section
        if current is not None:
            add(current, number, line)

    if not found_heading:
        clinical = [
            (n, line) for n, line in enumerate(lines, start=1) if not only_identifiers(line)
        ]
        for position, (number, line) in enumerate(clinical):
            if position < 2:
                add("Summary", number, line)
                continue
            for heading, looks in _LOOKS_LIKE.items():
                if looks.search(line):
                    add(heading, number, line)
                    break
    return [s for s in sections.values() if s.points]


# -- one note ---------------------------------------------------------------------------


async def summarize_note(text: str, model: ChatModel | None = None) -> NoteSummary:
    """Redact, number, summarize and check one note."""
    redaction = redact(text[:MAX_NOTE_CHARS])
    lines = note_lines(redaction.text)
    model = model if model is not None else ChatModel()
    notice: str | None = None
    if model.available and lines:
        numbered = "\n".join(f"[{i}] {line}" for i, line in enumerate(lines, start=1))
        messages = [
            ChatMessage("system", load_prompt(*PROMPT).text),
            ChatMessage("user", f"LINES:\n{numbered}"),
        ]
        try:
            result = await model.complete(messages, max_tokens=1400, temperature=0.1)
            sections, kept, removed, permille = await _checked(result.text, lines)
            if kept and permille <= _REJECT_FRACTION * 1000:
                return NoteSummary(
                    sections=sections,
                    lines=lines,
                    redactions=redaction.counts,
                    mode="llm",
                    model=f"{result.provider}:{result.model}",
                    removed=removed,
                )
            # Counts only — never the note's text.
            logger.warning(
                "note_summary_rejected", kept=kept, removed=removed, removed_permille=permille
            )
            notice = (
                "The AI summary didn't match the note closely enough, so this one is made of "
                "the note's own lines."
            )
        except LLMUnavailable:
            notice = (
                "The AI writer is busy right now, so this summary is made of the note's own lines."
            )
    elif not model.available:
        notice = (
            "No AI writer is configured on this server, so this summary is made of the note's "
            "own lines."
        )
    return NoteSummary(
        sections=extractive_summary(lines),
        lines=lines,
        redactions=redaction.counts,
        mode="extractive",
        model="extractive",
        notice=notice,
    )
