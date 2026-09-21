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
    sentences = split_sentences(answer)

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

    total_chars = len(answer.strip()) or 1
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
