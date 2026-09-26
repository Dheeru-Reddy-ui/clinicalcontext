"""Post-generation grounding verification.

Every clinical claim in a generated answer must be traceable to the passage it
cites. This splits the answer into sentences, decides which carry a clinical
claim, and checks each claim against its cited passage(s):

    SUPPORTED / PARTIALLY_SUPPORTED / UNSUPPORTED / NO_CITATION

Clinical sentences that are UNSUPPORTED or NO_CITATION are removed. If removing
them strips more than 30% of the answer, the whole answer is rejected and the
system abstains — a confident wrong answer is worse than none.

The verifier is pluggable: an offline lexical-entailment heuristic (default,
deterministic, testable) or an LLM verifier (versioned prompt). The heuristic
treats a fabricated number as decisive — a statistic not present in the cited
passage fails verification.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from app.schemas.guardrails import (
    GroundingVerdict,
    GuardrailFinding,
    SentenceSupport,
    SentenceVerdict,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'\[])")
_CITATION = re.compile(r"\[(\d+)\]")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?%?\b")
_WORD = re.compile(r"[a-z0-9]+")

# How models write citations, rewritten to the one form the checks read: [n],
# one number per bracket, before the sentence's full stop. A marker the checks
# cannot read leaves its claim uncited, and an answer with too many uncited
# claims is withheld (_REJECT_FRACTION).
_LENTICULAR_MARKER = re.compile(
    r"\N{LEFT BLACK LENTICULAR BRACKET}\s*(\d{1,2})[^\N{RIGHT BLACK LENTICULAR BRACKET}]*"
    r"\N{RIGHT BLACK LENTICULAR BRACKET}"
)
_NAMED_MARKER = re.compile(r"\[\s*(?:passage|source|ref(?:erence)?)\s*(\d{1,2})\s*\]", re.I)
_MARKER_GROUP = re.compile(
    r"\[\s*(\d{1,2}(?:\s*(?:[,;]|and|[-\N{EN DASH}\N{EM DASH}])\s*\d{1,2})+)\s*\]"
)
_GROUP_PART = re.compile(r"(\d{1,2})(?:\s*[-\N{EN DASH}\N{EM DASH}]\s*(\d{1,2}))?")
_MAX_MARKER_RANGE = 10
_MARKERS_AFTER_STOP = re.compile(r"([.!?])((?:\s*\[\d+\])+)")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+\N{BULLET}]|\d+[.)])\s+")
_HEADING = re.compile(r"^\s*(?:#{1,6}\s+|\*\*[^*]+\*\*:?\s*$)")
_TABLE_RULE = re.compile(r"^[\s|:\-]*-{2,}[\s|:\-]*$")

# A sentence is a clinical claim if it asserts something checkable: an effect,
# a recommendation, a statistic, or a drug/dose. Pure framing is not.
_CLINICAL_SIGNAL = re.compile(
    r"\b(?:reduc|increas|decreas|lower|rais|improv|worsen|superior|inferior|"
    r"associat|risk|efficac|effective|mortalit|morbidit|benefit|harm|adverse|"
    r"recommend|first[-\s]?line|second[-\s]?line|indicated|contraindicated|"
    r"dose|dosage|mg|mcg|ml|units|mmhg|hazard\s+ratio|odds\s+ratio|"
    r"relative\s+risk|confidence\s+interval|\bci\b|p\s*[<=]|significant)\w*",
    re.I,
)
_FRAMING = re.compile(
    r"^(?:here(?:'s| is)|in summary|to summarize|overall|the following|"
    r"based on the|according to the (?:retrieved|provided) (?:literature|evidence|passages))",
    re.I,
)
_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "of",
        "to",
        "in",
        "and",
        "or",
        "for",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "on",
        "at",
        "by",
        "as",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "their",
        "from",
        "than",
        "then",
        "also",
        "may",
        "can",
        "could",
        "should",
        "would",
        "has",
        "have",
        "had",
        "not",
        "no",
        "into",
        "within",
        "between",
        "per",
    ]
)

_REJECT_FRACTION = 0.30


def split_sentences(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT.split(stripped) if s.strip()]


def as_sentences(markdown: str) -> str:
    """Markdown as plain statements, one per line: a heading or a bullet's
    dash would otherwise hide where one statement ends and the next begins.
    Headings and table rules are dropped, a table row reads as its cells,
    bold markers go, and a line without a full stop gets one."""
    lines: list[str] = []
    for line in markdown.splitlines():
        if not line.strip() or _HEADING.match(line) or _TABLE_RULE.match(line):
            continue
        text = line.strip()
        if text.startswith("|"):
            text = "; ".join(cell.strip() for cell in text.strip("|").split("|") if cell.strip())
        text = _LIST_ITEM.sub("", text).replace("**", "").strip().removesuffix(":")
        if not text:
            continue
        if text[-1] not in ".!?":
            text += "."
        lines.append(text)
    return "\n".join(lines)


def _expanded_group(match: re.Match[str]) -> str:
    numbers: list[int] = []
    for part in _GROUP_PART.finditer(match.group(1)):
        low = int(part.group(1))
        high = int(part.group(2)) if part.group(2) else low
        if low <= high <= low + _MAX_MARKER_RANGE:
            numbers.extend(range(low, high + 1))
        else:
            numbers.extend((low, high))
    return "".join(f"[{n}]" for n in dict.fromkeys(numbers))


def _inside_stop(match: re.Match[str]) -> str:
    markers = "".join(f"[{n}]" for n in _CITATION.findall(match.group(2)))
    return f" {markers}{match.group(1)}"


def normalize_answer(text: str) -> str:
    """An answer as the checks read it: every citation written as ``[n]``
    ("[1, 3]" → "[1][3]", "[2-4]" → "[2][3][4]", "[Passage 2]" → "[2]")
    and moved inside its sentence's full stop ("reduced mortality. [2]" →
    "reduced mortality [2]."), then markdown flattened into one statement per
    line. A marker after the stop would otherwise be read as the start of the
    next sentence, leaving its own claim uncited."""
    text = _LENTICULAR_MARKER.sub(r"[\1]", text)
    text = _NAMED_MARKER.sub(r"[\1]", text)
    text = _MARKER_GROUP.sub(_expanded_group, text)
    text = _MARKERS_AFTER_STOP.sub(_inside_stop, text)
    return as_sentences(text)


def is_clinical_claim(sentence: str) -> bool:
    """A sentence is a clinical claim if it is not pure framing and either
    carries a citation marker (an evidential assertion by construction) or
    contains clinical-signal vocabulary (so uncited claims are still caught)."""
    if _FRAMING.search(sentence.strip()):
        return False
    if _CITATION.search(sentence):
        return True
    return bool(_CLINICAL_SIGNAL.search(sentence))


def _content_tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1}


def invented_numbers(sentence: str, passages: list[str]) -> set[str]:
    """The figures in ``sentence`` (citation markers aside) that none of
    ``passages`` contains. The verifier above only withholds "supported" from
    a sentence like that — it can still be "partially supported" — so a tool
    that must never pass on a made-up figure (the note summarizer, the
    tutor's explanations) checks this as well."""
    claimed = set(_NUMBER.findall(_CITATION.sub(" ", sentence)))
    if not claimed:
        return set()
    found = set(_NUMBER.findall(" ".join(passages)))
    return claimed - found


@runtime_checkable
class GroundingVerifier(Protocol):
    name: str

    async def verify(self, sentence: str, passages: list[str]) -> SentenceSupport: ...


class LexicalGroundingVerifier:
    """Offline entailment heuristic: token overlap + a fabricated-number veto."""

    name = "lexical"

    supported_threshold: float = 0.55
    partial_threshold: float = 0.25

    async def verify(self, sentence: str, passages: list[str]) -> SentenceSupport:
        if not passages:
            return "unsupported"
        clean_sentence = _CITATION.sub(" ", sentence)
        sentence_tokens = _content_tokens(clean_sentence)
        if not sentence_tokens:
            return "supported"  # nothing substantive to contradict
        passage_text = " ".join(passages)
        passage_tokens = _content_tokens(passage_text)

        overlap = len(sentence_tokens & passage_tokens) / len(sentence_tokens)

        # A statistic in the claim must appear in the cited passage, else the
        # claim is (at best) partially supported — fabricated numbers are the
        # most dangerous failure. Citation markers are stripped first so a
        # "[1]" is not mistaken for a claimed statistic.
        claim_numbers = set(_NUMBER.findall(clean_sentence))
        passage_numbers = set(_NUMBER.findall(passage_text))
        fabricated_number = bool(claim_numbers) and not claim_numbers.issubset(passage_numbers)

        if overlap >= self.supported_threshold and not fabricated_number:
            return "supported"
        if overlap >= self.partial_threshold:
            return "partially_supported"
        return "unsupported"


class LLMGroundingVerifier:
    """LLM entailment verifier (versioned prompt). Falls back to lexical on any
    provider error, so grounding never silently passes on an outage."""

    name = "llm"

    def __init__(self) -> None:
        self._fallback = LexicalGroundingVerifier()

    async def verify(self, sentence: str, passages: list[str]) -> SentenceSupport:
        if not passages:
            return "unsupported"
        from anthropic import AsyncAnthropic, AuthenticationError

        from app.config import get_settings
        from app.prompts.loader import load_prompt

        prompt = load_prompt("grounding_verifier", 1)
        joined = "\n\n".join(f"[{i + 1}] {p}" for i, p in enumerate(passages))
        try:
            client = AsyncAnthropic(api_key=get_settings().anthropic_api_key.get_secret_value())
            response = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=16,
                temperature=0.0,
                system=prompt.text,
                messages=[{"role": "user", "content": f"CLAIM: {sentence}\n\nPASSAGES:\n{joined}"}],
            )
        except (AuthenticationError, Exception):
            return await self._fallback.verify(sentence, passages)
        raw = "".join(b.text for b in response.content if b.type == "text").strip().lower()
        if "partial" in raw:
            return "partially_supported"
        if "unsupported" in raw or "not support" in raw:
            return "unsupported"
        if "supported" in raw:
            return "supported"
        return await self._fallback.verify(sentence, passages)


async def verify_grounding(
    answer: str,
    citations: dict[int, str],
    *,
    verifier: GroundingVerifier | None = None,
) -> GroundingVerdict:
    """Verify an answer against its citation passages and prune/abstain.

    ``citations`` maps a marker number (the ``[3]`` in the text) to that
    passage's text.
    """
    resolved = verifier if verifier is not None else LexicalGroundingVerifier()
    normalized = normalize_answer(answer)
    sentences = [s for line in normalized.splitlines() for s in split_sentences(line)]

    verdicts: list[SentenceVerdict] = []
    for index, sentence in enumerate(sentences):
        markers = [int(m) for m in _CITATION.findall(sentence)]
        clinical = is_clinical_claim(sentence)

        if not clinical:
            support: SentenceSupport = "supported"
        elif not markers:
            support = "no_citation"
        else:
            cited_passages = [citations[m] for m in markers if m in citations]
            support = (
                "no_citation"
                if not cited_passages
                else await resolved.verify(sentence, cited_passages)
            )

        verdicts.append(
            SentenceVerdict(
                index=index,
                text=sentence,
                is_clinical_claim=clinical,
                support=support,
                cited_markers=markers,
            )
        )

    removed = [
        v for v in verdicts if v.is_clinical_claim and v.support in ("unsupported", "no_citation")
    ]
    kept_sentences = [v for v in verdicts if v not in removed]
    kept_answer = " ".join(v.text for v in kept_sentences).strip()

    total_chars = len(normalized.strip()) or 1
    removed_chars = sum(len(v.text) for v in removed)
    removed_fraction = removed_chars / total_chars

    if removed_fraction > _REJECT_FRACTION:
        finding = GuardrailFinding(
            stage="grounding",
            action="block",
            code="grounding_rejected",
            message=(
                "The generated answer could not be sufficiently grounded in the "
                "retrieved evidence and was withheld. The system is abstaining "
                "rather than presenting unverified clinical claims."
            ),
            detail={
                "removed_fraction": round(removed_fraction, 3),
                "removed_sentences": len(removed),
                "total_sentences": len(verdicts),
            },
        )
        return GroundingVerdict(
            accepted=False,
            finding=finding,
            kept_answer="",
            removed_sentences=removed,
            sentence_verdicts=verdicts,
            removed_fraction=round(removed_fraction, 3),
        )

    code = "grounding_pruned" if removed else "grounding_ok"
    finding = GuardrailFinding(
        stage="grounding",
        action="allow",
        code=code,
        message=(
            "Some unsupported sentences were removed from the answer."
            if removed
            else "All clinical claims were grounded in their cited passages."
        ),
        detail={
            "removed_sentences": len(removed),
            "total_sentences": len(verdicts),
            "removed_fraction": round(removed_fraction, 3),
        },
    )
    return GroundingVerdict(
        accepted=True,
        finding=finding,
        kept_answer=kept_answer,
        removed_sentences=removed,
        sentence_verdicts=verdicts,
        removed_fraction=round(removed_fraction, 3),
    )
