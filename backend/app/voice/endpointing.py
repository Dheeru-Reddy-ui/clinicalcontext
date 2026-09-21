"""Turn-taking: the layered endpoint decision (11C) and backchannel filter.

    Layer 1 — acoustic: a silence candidate opens after ``base_ms`` of no speech.
    Layer 2 — semantic: is the utterance so far a complete question? If not,
              the window is extended to ``extended_ms`` (the clinician is
              thinking: "in a patient with, um… stage 4 CKD…").
    Layer 3 — ceiling: ``ceiling_ms`` of silence commits regardless. Never hang.

The completeness classifier has a deterministic implementation (the backbone
that the hesitation fixtures test) and an optional Claude Haiku tier behind
the same Protocol (10-token output, versioned prompt). The LLM tier can only
*extend* a window the heuristic would have committed — it never shortens one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol

import structlog

logger = structlog.stdlib.get_logger("app.voice.endpointing")

EndpointLayer = Literal["vad", "semantic", "ceiling", "text"]

# Words a complete clinical question does not end on.
_DANGLING_TAIL = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "for",
        "in",
        "on",
        "at",
        "to",
        "with",
        "without",
        "and",
        "or",
        "but",
        "versus",
        "vs",
        "than",
        "as",
        "by",
        "from",
        "into",
        "about",
        "between",
        "among",
        "over",
        "under",
        "after",
        "before",
        "during",
        "per",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "can",
        "could",
        "should",
        "would",
        "will",
        "may",
        "might",
        "must",
        "if",
        "when",
        "whether",
        "while",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "how",
        "why",
        "where",
        "um",
        "uh",
        "er",
        "hmm",
        "like",
        "so",
        "um,",
        "uh,",
        "er,",
        "i",
        "my",
        "this",
        "that",
        "these",
        "those",
        "patient",
        "patients",
        "regarding",
        "concerning",
        "including",
        "except",
        "plus",
        "minus",
    ]
)
_DANGLING_PUNCT = (",", "-", "—", "…", ";", ":", "(")
_FILLERS = frozenset({"um", "uh", "er", "erm", "hmm", "uhh", "umm"})
_CHOICE_REPLY = re.compile(
    r"^(?:the\s+)?(?:first|second|former|latter|one|two)(?:\s+one)?[.!?]?$"
    r"|^number\s+(?:one|two|1|2)[.!?]?$",
    re.I,
)
_FRAGMENT_OPENER = re.compile(
    r"^(?:in|for|with|on|at|after|before|during|regarding|concerning|about)\s+"
    r"(?:a|an|the|my|this)\b",
    re.I,
)
_QUESTION_OPENERS = re.compile(
    r"^(?:what|which|when|where|why|how|who|whom|whose|is|are|does|do|did|can|could|"
    r"should|would|will|has|have|was|were|may|might|compare|tell|give|summari[sz]e|"
    r"explain|list|describe|any|okay|ok|please)\b",
    re.I,
)
_IMPERATIVE = re.compile(
    r"^(?:tell me|give me|compare|summari[sz]e|explain|list|describe|show me|walk me)\b", re.I
)
_HAS_CLINICAL_OBJECT = re.compile(
    r"\b(?:treat|treatment|therap|manage|management|dose|dosing|evidence|guideline|"
    r"recommend|risk|effect|efficacy|safety|outcome|mortality|prevention|diagnos|"
    r"screening|first[- ]line|second[- ]line|compared?|versus|vs\.?|trial|study|"
    r"criteria|prognosis|survival|cause|associated|safe|effective|recommended|"
    r"indicated|contraindicated|superior|better)\b",
    re.I,
)
# A predicate word that can close a short question ("is metformin safe").
_COMPLETE_TAIL = re.compile(
    r"\b(?:safe|effective|efficacious|recommended|indicated|better|superior|useful|"
    r"beneficial|harmful|contraindicated|help|helps|work|works)\b\s*$",
    re.I,
)


@dataclass(slots=True)
class CompletenessVerdict:
    complete: bool
    reason: str
    method: Literal["heuristic", "llm"]


class CompletenessClassifier(Protocol):
    async def classify(self, utterance: str) -> CompletenessVerdict: ...


def heuristic_completeness(utterance: str) -> CompletenessVerdict:
    text = utterance.strip()
    if not text:
        return CompletenessVerdict(False, "empty", "heuristic")
    lowered = text.lower().rstrip(" ?.!")
    words = re.findall(r"[a-z0-9'\u2019-]+", lowered)
    if not words:
        return CompletenessVerdict(False, "no_words", "heuristic")
    # A trailing "?" or "." is the recognizer's guess at punctuation — streaming
    # recognizers append one to any question-shaped fragment — so it carries
    # no weight here; the words decide. Dangling punctuation still does.
    if text.rstrip().endswith(_DANGLING_PUNCT):
        return CompletenessVerdict(False, "dangling_punctuation", "heuristic")
    last = words[-1].strip("'\u2019")
    if last in _DANGLING_TAIL:
        return CompletenessVerdict(False, f"dangling_word:{last}", "heuristic")
    if any(w in _FILLERS for w in words[-3:]):
        return CompletenessVerdict(False, "recent_filler", "heuristic")
    if _CHOICE_REPLY.match(lowered):
        return CompletenessVerdict(True, "choice_reply", "heuristic")
    if _FRAGMENT_OPENER.match(lowered) and not _QUESTION_OPENERS.match(lowered):
        # "in a patient with stage 4 CKD": a subordinate clause — the question
        # is still coming. Costs a longer window, never a cut-off — unless the
        # question already arrived after the last comma ("…, is metformin safe").
        _head, comma, tail = lowered.rpartition(",")
        if comma:
            question = heuristic_completeness(tail)
            if question.complete and question.reason.startswith("question_"):
                return CompletenessVerdict(True, f"subordinate_then_{question.reason}", "heuristic")
        return CompletenessVerdict(False, "subordinate_opener", "heuristic")
    if _QUESTION_OPENERS.match(lowered) and _COMPLETE_TAIL.search(lowered) and len(words) >= 3:
        # "is metformin safe", "does aspirin help": a predicate closes it.
        return CompletenessVerdict(True, "question_with_predicate", "heuristic")
    if len(words) <= 3 and not _IMPERATIVE.match(lowered):
        # "yes", "continue", "the second one": short replies are complete;
        # a question stub ("what is", "does apixaban") is not.
        if _QUESTION_OPENERS.match(lowered):
            return CompletenessVerdict(False, "question_stub", "heuristic")
        return CompletenessVerdict(True, "short_reply", "heuristic")
    if _QUESTION_OPENERS.match(lowered) or _IMPERATIVE.match(lowered):
        if _HAS_CLINICAL_OBJECT.search(lowered) and len(words) >= 4:
            return CompletenessVerdict(True, "question_with_object", "heuristic")
        if len(words) >= 6:
            return CompletenessVerdict(True, "long_question", "heuristic")
        return CompletenessVerdict(False, "question_without_object", "heuristic")
    if len(words) >= 5:
        return CompletenessVerdict(True, "statement", "heuristic")
    return CompletenessVerdict(False, "fragment", "heuristic")


class HeuristicCompletenessClassifier:
    async def classify(self, utterance: str) -> CompletenessVerdict:
        return heuristic_completeness(utterance)


class LLMCompletenessClassifier:
    """Claude Haiku, ≤10 output tokens, versioned prompt ``voice_completeness``.

    Fail-safe: any provider error falls back to the heuristic verdict, and an
    LLM "complete" never overrides a heuristic "incomplete" (it can only make
    the agent wait longer, never cut the user off).
    """

    def __init__(self, *, model: str = "claude-haiku-4-5-20251001") -> None:
        self._model = model

    async def classify(self, utterance: str) -> CompletenessVerdict:
        base = heuristic_completeness(utterance)
        if not base.complete:
            return base
        try:
            from anthropic import AsyncAnthropic

            from app.config import get_settings
            from app.prompts.loader import load_prompt

            prompt = load_prompt("voice_completeness", 1)
            client = AsyncAnthropic(api_key=get_settings().anthropic_api_key.get_secret_value())
            response = await client.messages.create(
                model=self._model,
                max_tokens=10,
                temperature=0.0,
                system=prompt.text,
                messages=[{"role": "user", "content": utterance}],
            )
            raw = "".join(b.text for b in response.content if b.type == "text").strip().lower()
        except Exception as exc:
            logger.warning("completeness_llm_failed", error=f"{type(exc).__name__}: {exc}")
            return base
        if raw.startswith("incomplete"):
            return CompletenessVerdict(False, "llm_incomplete", "llm")
        return CompletenessVerdict(True, base.reason, "llm")


@dataclass(slots=True)
class EndpointPolicy:
    base_ms: int = 300
    extended_ms: int = 1200
    ceiling_ms: int = 2000

    def decide(
        self, silence_ms: float, completeness: CompletenessVerdict | None
    ) -> tuple[bool, EndpointLayer]:
        """→ (commit?, deciding layer) for the current silence run."""
        if silence_ms >= self.ceiling_ms:
            return True, "ceiling"
        if silence_ms < self.base_ms:
            return False, "vad"
        if completeness is None:
            return False, "semantic"  # verdict still pending
        if completeness.complete:
            return True, "vad" if silence_ms < self.extended_ms else "semantic"
        if silence_ms >= self.extended_ms:
            return True, "semantic"
        return False, "semantic"


# -- backchannels (11C.3) ------------------------------------------------------------

_BACKCHANNEL_WORDS = frozenset(
    [
        "mm",
        "mhm",
        "mmhmm",
        "mm-hmm",
        "uh-huh",
        "uhhuh",
        "uh",
        "huh",
        "okay",
        "ok",
        "yeah",
        "yep",
        "yes",
        "right",
        "sure",
        "alright",
        "aha",
        "ah",
        "oh",
        "hm",
        "hmm",
        "gotcha",
        "i",
        "see",
        "got",
        "it",
        "fine",
        "good",
        "great",
        "cool",
    ]
)


def is_backchannel(text: str, duration_ms: float | None = None) -> bool:
    """Short affirmations while the agent speaks are not interruptions."""
    words = [w.strip(".,!?") for w in text.lower().split()]
    words = [w for w in words if w]
    if not words:
        return True
    if duration_ms is not None and duration_ms > 1200:
        return False
    if len(words) > 3:
        return False
    return all(w in _BACKCHANNEL_WORDS for w in words)


_CONTINUE = re.compile(
    r"^(?:(?:yes|yeah|yep|sure|ok|okay|please|go on|go ahead|continue|carry on|keep going|"
    r"walk me through|both|tell me more|more|sorry|sorry,?\s*continue|resume)[\s.,!]*)+$",
    re.I,
)
_DECLINE = re.compile(
    r"^(?:no|nope|no thanks|that's fine|that's enough|stop|skip|next)[\s.,!]*$", re.I
)


def classify_reply(text: str) -> Literal["continue", "decline", "other"]:
    """After an offer or an interruption: did the user ask to go on?"""
    cleaned = text.strip()
    if _CONTINUE.match(cleaned):
        return "continue"
    if _DECLINE.match(cleaned):
        return "decline"
    return "other"
