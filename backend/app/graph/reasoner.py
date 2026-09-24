"""The reasoning operations behind the graph nodes.

Every LLM-driven step is a method on a :class:`Reasoner`, with two
implementations:

- :class:`HeuristicReasoner` — deterministic, offline, no API key. It derives
  its decisions from the *actual* retrieved passages and their metadata (not
  hardcoded answers): grading from retrieval scores, contradiction from
  recommendation polarity + publication year, generation by *extraction* of
  real cited passages (grounded by construction). This lets the whole graph —
  and all four Phase-8 gates — run and be tested without a paid key.
- :class:`LLMReasoner` — Claude (claude-sonnet-4-6) with versioned prompts,
  falling back to the heuristic on any provider error, so an outage degrades
  gracefully instead of failing the run.
- :class:`FreeLLMReasoner` — the heuristic's decisions with a free-tier model
  (Groq / Cerebras, see ``app.llm.chat``) writing the answer.

Which one is active is a config/runtime choice; the graph is identical.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, cast, runtime_checkable

import structlog

from app.graph.cost import UsageAccumulator
from app.llm.chat import ChatMessage, ChatModel, ChatResult, LLMUnavailable
from app.retrieval.types import RetrievedChunk
from app.schemas.answer import Contradiction, ContradictionPosition, QueryType, RetrievalGrade
from app.services import cost

logger = structlog.stdlib.get_logger("app.graph.reasoner")

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
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
        "on",
        "at",
        "by",
        "as",
        "that",
        "this",
        "does",
        "do",
        "what",
        "which",
        "how",
        "much",
        "many",
        "should",
        "i",
        "my",
        "patient",
        "given",
        "these",
        "vs",
        "versus",
    ]
)

# Retrieval-grade thresholds on the top boosted score (cosine-ish, 0..1-ish).
_SUFFICIENT_TOP = 0.45
_IRRELEVANT_TOP = 0.18
_MIN_CHUNKS = 3
# The topic of a clinical question is what is left once its boilerplate is
# removed: "What antibiotics are recommended for Lyme disease?" is about
# antibiotics and Lyme, not about being recommended or being a disease. A
# retrieval is only sufficient when the passages carry that topic.
_GENERIC = frozenset(
    """
    treatment treatments treat treated treating therapy therapies therapeutic management
    managed manage managing recommended recommend recommendation recommendations guideline
    guidelines current first line first-line second second-line effective effectiveness
    efficacy efficacious evidence safe safety risk risks patient patients disease diseases
    disorder disorders condition conditions prognosis prognostic outcome outcomes diagnosed
    diagnosis diagnostic diagnose prevention prevented prevent preventing use used using given
    give dose doses dosing role accurate accuracy compare compared comparison better best diagnosing
    improve improves improved common factor factors predict predicts predicting syndrome
    acute chronic clinical study studies trial trials evidence-based indicated should
    versus recommended options option choice preferred benefit benefits increase increases
    reduce reduces reduced reduction rate rates level levels care primary secondary
    """.split()  # noqa: SIM905 — a word list reads as prose
)
_TOPIC_COVERAGE_SUFFICIENT = 0.67
_TOPIC_COVERAGE_IRRELEVANT = 0.34


@dataclass(slots=True)
class GenerationOutput:
    text: str
    # Chunks in citation order; marker n = ordered_chunks[n-1].
    ordered_chunks: list[RetrievedChunk]
    mode: str = "extractive"


@runtime_checkable
class Reasoner(Protocol):
    name: str
    usage: UsageAccumulator

    async def classify(self, query: str) -> tuple[QueryType, bool]: ...
    async def decompose(self, query: str) -> list[str]: ...
    async def grade_retrieval(self, query: str, chunks: list[RetrievedChunk]) -> RetrievalGrade: ...
    async def rewrite_query(
        self, query: str, attempt: int, chunks: list[RetrievedChunk]
    ) -> str: ...
    async def detect_contradiction(
        self, query: str, chunks: list[RetrievedChunk]
    ) -> Contradiction: ...
    async def generate(
        self, query: str, chunks: list[RetrievedChunk], contradiction: Contradiction
    ) -> GenerationOutput: ...


@runtime_checkable
class StreamingReasoner(Protocol):
    """Optional capability: generation as a token stream. The voice path's
    sentence pipeline consumes the deltas; the final item is the output."""

    def generate_stream(
        self, query: str, chunks: list[RetrievedChunk], contradiction: Contradiction
    ) -> AsyncIterator[str | GenerationOutput]: ...


# -- shared helpers ------------------------------------------------------------------

_COMPARISON = re.compile(
    r"\b(?:vs\.?|versus|compared\s+with|compared\s+to|preferred\s+over|"
    r"better\s+than|superior\s+to|or\s+warfarin|disagree)\b",
    re.I,
)
_RECOMMEND_POS = re.compile(
    r"\b(?:recommend|first[-\s]?line|preferred|superior|effective|benefit|"
    r"should\s+be\s+used|indicated|reduces?\s+(?:risk|mortality))\b",
    re.I,
)
_RECOMMEND_NEG = re.compile(
    r"\b(?:not\s+recommend|no\s+benefit|insufficient\s+evidence|inferior|"
    r"should\s+not|avoid|contraindicated|no\s+significant|did\s+not\s+reduce|"
    r"lack\s+of\s+evidence|against\s+(?:the\s+)?use)\b",
    re.I,
)
_QUERY_TYPE_SIGNALS: list[tuple[QueryType, re.Pattern[str]]] = [
    (
        "guideline_comparison",
        re.compile(
            r"\b(?:guideline|guidelines|recommendation|consensus).*\b(?:disagree|differ|vs|versus|compare|conflict|threshold)",
            re.I,
        ),
    ),
    (
        "harm",
        re.compile(
            r"\b(?:side\s+effect|adverse|harm|safety|toxicity|risk\s+of|bleeding\s+risk)\b", re.I
        ),
    ),
    (
        "prognosis",
        re.compile(
            r"\b(?:prognos|survival|mortality\s+(?:rate|risk)|life\s+expectancy|outcome\s+of)\b",
            re.I,
        ),
    ),
    (
        "etiology",
        re.compile(
            r"\b(?:cause|causes|etiolog|risk\s+factor|pathogenesis|associated\s+with)\b", re.I
        ),
    ),
    (
        "diagnosis",
        re.compile(
            r"\b(?:diagnos|differential|what\s+condition|criteria\s+for\s+diagnosing)\b", re.I
        ),
    ),
    (
        "therapy",
        re.compile(
            r"\b(?:treat|therapy|management|first[-\s]?line|dose|dosing|regimen|preferred\s+over|manage)\b",
            re.I,
        ),
    ),
]


def content_tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if t not in _STOP and len(t) > 2}


def _stem(token: str) -> str:
    """Just enough to let "antibiotics" meet "antibiotic"."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("es") and token[-3] in "sxz":
        return token[:-2]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


_GENERIC_STEMS = frozenset(_stem(w) for w in _GENERIC)


def is_generic_word(token: str) -> bool:
    """A word that says how a question is asked, not what it is about."""
    lowered = token.lower()
    return lowered in _STOP or _stem(lowered) in _GENERIC_STEMS


def _stems(text: str) -> list[str]:
    return [_stem(t) for t in _WORD.findall(text.lower())]


def topic_phrases(query: str) -> list[tuple[str, ...]]:
    """The query's specific terms, kept in their phrases: "post-exposure
    prophylaxis" is one topic, not three common words. Boilerplate and
    stopwords break a phrase."""
    phrases: list[tuple[str, ...]] = []
    run: list[str] = []
    for token in _stems(query):
        specific = token not in _STOP and token not in _GENERIC_STEMS and len(token) >= 4
        if specific:
            run.append(token)
        elif run:
            phrases.append(tuple(run))
            run = []
    if run:
        phrases.append(tuple(run))
    if phrases:
        return phrases
    return [(t,) for t in _stems(query) if t not in _STOP and len(t) > 2]


def topic_tokens(query: str) -> set[str]:
    return {t for phrase in topic_phrases(query) for t in phrase}


def _contains(haystack: list[str], needle: tuple[str, ...]) -> bool:
    size = len(needle)
    return any(tuple(haystack[i : i + size]) == needle for i in range(len(haystack) - size + 1))


def _phrase_present(phrase: tuple[str, ...], passages: list[list[str]]) -> bool:
    """A one- or two-word topic must appear whole; a longer one counts when any
    adjacent pair of its words does ("fine-needle aspiration cytology" still
    answers a question about fine-needle aspiration biopsy)."""
    needles = [phrase] if len(phrase) <= 2 else [phrase[i : i + 2] for i in range(len(phrase) - 1)]
    return any(_contains(p, n) for p in passages for n in needles)


def chunk_relevance(query: str, chunk: RetrievedChunk) -> float:
    """The share of the query's topic phrases this one passage carries.

    Coverage (below) asks whether the top passages carry the topic *between
    them*, which one on-topic passage among five unrelated ones satisfies;
    an answer built from all five would then cite the unrelated four. This
    is the per-passage test that keeps them out."""
    phrases = topic_phrases(query)
    if not phrases:
        return 1.0
    passage = [_stems(f"{chunk.title or ''} {chunk.content}")]
    return sum(1 for phrase in phrases if _phrase_present(phrase, passage)) / len(phrases)


def topic_coverage(query: str, chunks: list[RetrievedChunk], *, top: int = 5) -> float:
    """The share of the query's topic phrases the top passages carry between
    them (a comparison's two sides may sit in two passages)."""
    phrases = topic_phrases(query)
    if not phrases:
        return 1.0
    passages = [_stems(f"{c.title or ''} {c.content}") for c in chunks[:top]]
    return sum(1 for phrase in phrases if _phrase_present(phrase, passages)) / len(phrases)


def _top_score(chunks: list[RetrievedChunk]) -> float:
    return max((c.score for c in chunks), default=0.0)


class HeuristicReasoner:
    """Deterministic reasoning from real passages + metadata. No API key."""

    name = "heuristic"

    def __init__(self) -> None:
        self.usage = UsageAccumulator()  # always empty → truthful $0 cost

    async def classify(self, query: str) -> tuple[QueryType, bool]:
        query_type: QueryType = "other"
        for candidate, pattern in _QUERY_TYPE_SIGNALS:
            if pattern.search(query):
                query_type = candidate
                break
        multi_hop = bool(_COMPARISON.search(query)) or (
            query.lower().count(" and ") >= 1
            and (query_type in ("therapy", "guideline_comparison", "harm"))
        )
        # A comparison is inherently a guideline/therapy comparison.
        if _COMPARISON.search(query) and query_type in ("other", "therapy"):
            query_type = "guideline_comparison" if "guideline" in query.lower() else query_type
        return query_type, bool(multi_hop)

    async def decompose(self, query: str) -> list[str]:
        match = _COMPARISON.search(query)
        if not match:
            return [query]
        left = query[: match.start()].strip(" ,.?")
        right = query[match.end() :].strip(" ,.?")
        # Try to keep a shared context clause (after "in ..."/"with ...").
        context = ""
        ctx_match = re.search(r"\b(?:in|with|for)\b\s+(.*)$", right)
        if ctx_match:
            context = ctx_match.group(1).strip(" ?.")
            right_entity = right[: ctx_match.start()].strip()
        else:
            right_entity = right
        left_entity = re.sub(r"^(?:is|are|should|does|do|which|what)\b\s*", "", left, flags=re.I)
        subs: list[str] = []
        if left_entity:
            subs.append(f"{left_entity} in {context}".strip() if context else left_entity)
        if right_entity:
            subs.append(f"{right_entity} in {context}".strip() if context else right_entity)
        subs.append(query)  # the head-to-head question itself
        # De-dupe, keep order, drop empties.
        seen: set[str] = set()
        result: list[str] = []
        for sub in subs:
            if sub and sub not in seen:
                seen.add(sub)
                result.append(sub)
        return result or [query]

    async def grade_retrieval(self, query: str, chunks: list[RetrievedChunk]) -> RetrievalGrade:
        if not chunks:
            return "irrelevant"
        top = _top_score(chunks)
        # Are the passages about the question's topic at all? A high score
        # on generic vocabulary ("treatment", "recommended") is not evidence.
        coverage = topic_coverage(query, chunks)
        if top < _IRRELEVANT_TOP or coverage < _TOPIC_COVERAGE_IRRELEVANT:
            return "irrelevant"
        if (
            top >= _SUFFICIENT_TOP
            and len(chunks) >= _MIN_CHUNKS
            and coverage >= _TOPIC_COVERAGE_SUFFICIENT
        ):
            return "sufficient"
        return "insufficient"

    async def rewrite_query(self, query: str, attempt: int, chunks: list[RetrievedChunk]) -> str:
        if attempt == 0:
            # Broaden: strip parenthetical/qualifier tails and comparison framing.
            broadened = re.sub(r"\([^)]*\)", "", query)
            broadened = re.sub(r"\b(?:in|with)\s+[a-z0-9\s-]+$", "", broadened, flags=re.I)
            return broadened.strip(" ,.?") or query
        # attempt 1 — heuristic HyDE: a declarative pseudo-answer to embed.
        terms = " ".join(sorted(content_tokens(query))[:8])
        return f"Clinical evidence and guideline recommendations regarding {terms}."

    async def detect_contradiction(self, query: str, chunks: list[RetrievedChunk]) -> Contradiction:
        positive: list[tuple[int, RetrievedChunk]] = []
        negative: list[tuple[int, RetrievedChunk]] = []
        for marker, chunk in enumerate(chunks, start=1):
            text = chunk.content
            has_pos = bool(_RECOMMEND_POS.search(text))
            has_neg = bool(_RECOMMEND_NEG.search(text))
            if has_neg:  # negation dominates (it usually contains the recommend verb)
                negative.append((marker, chunk))
            elif has_pos:
                positive.append((marker, chunk))

        if not positive or not negative:
            return Contradiction(detected=False, axis="none")

        # Shared topic? require lexical overlap between the two clusters so we
        # don't call unrelated passages a "contradiction".
        pos_tokens = set().union(*(content_tokens(c.content) for _, c in positive))
        neg_tokens = set().union(*(content_tokens(c.content) for _, c in negative))
        shared = pos_tokens & neg_tokens
        if len(shared) < 3:
            return Contradiction(detected=False, axis="none")

        pos_years = [c.publication_date.year for _, c in positive if c.publication_date]
        neg_years = [c.publication_date.year for _, c in negative if c.publication_date]
        axis: str = "unclear"
        if pos_years and neg_years and abs(max(pos_years) - max(neg_years)) >= 2:
            axis = "temporal"

        positions = [
            ContradictionPosition(
                stance="supports / recommends",
                markers=[m for m, _ in positive],
                year=max(pos_years) if pos_years else None,
            ),
            ContradictionPosition(
                stance="does not support / recommends against",
                markers=[m for m, _ in negative],
                year=max(neg_years) if neg_years else None,
            ),
        ]
        topic = ", ".join(sorted(shared)[:4])
        explanation = f"Retrieved sources disagree on {topic}. " + (
            "The difference tracks publication year — the more recent sources reach a "
            "different conclusion, suggesting the evidence or recommendation evolved."
            if axis == "temporal"
            else "The sources reach opposing conclusions; the difference may reflect "
            "different populations, endpoints, or study designs."
        )
        return Contradiction(detected=True, positions=positions, axis=axis, explanation=explanation)

    async def generate(
        self, query: str, chunks: list[RetrievedChunk], contradiction: Contradiction
    ) -> GenerationOutput:
        """Extractive generation: assemble the answer from real cited passages,
        so every clinical sentence is grounded by construction."""
        used = chunks[:6]
        # The extractive path is free, and says so: the units it would have
        # sent to a model are recorded at $0 for the projection.
        cost.record(
            "generation",
            provider="local",
            model=self.name,
            units=sum(_approx_tokens(c.content) for c in used),
            unit="tokens",
        )
        if contradiction.detected:
            return self._generate_conflict(used, contradiction)
        # Framing is its own sentence: joined to the first claim by a colon it
        # would swallow that claim's grounding check.
        lines = ["Based on the retrieved literature, the evidence is as follows."]
        for marker, chunk in enumerate(used, start=1):
            lines.append(_cite(_lead_sentence(chunk.content), marker))
        return GenerationOutput(text=" ".join(lines), ordered_chunks=used, mode="extractive")

    async def generate_stream(
        self, query: str, chunks: list[RetrievedChunk], contradiction: Contradiction
    ) -> AsyncIterator[str | GenerationOutput]:
        """Extractive generation is instantaneous; it is replayed sentence by
        sentence so the streaming consumers are exercised identically."""
        output = await self.generate(query, chunks, contradiction)
        for piece in re.split(r"(?<=[.!?]\])\s+|(?<=[.!?])\s+(?=[A-Z])", output.text):
            if piece:
                yield piece + " "
        yield output

    def _generate_conflict(
        self, chunks: list[RetrievedChunk], contradiction: Contradiction
    ) -> GenerationOutput:
        by_marker = {i + 1: c for i, c in enumerate(chunks)}
        # Framing (matches the grounding verifier's framing rule) + cited
        # position sentences only. The free-text explanation lives in the
        # structured Contradiction object, NOT the answer body, so no uncited
        # clinical sentence is introduced for the grounding check to reject.
        parts = ["Based on the retrieved evidence, the sources disagree, so this is a conflict."]
        for position in contradiction.positions:
            marker = next((m for m in position.markers if m in by_marker), None)
            if marker is None:
                continue
            year = f" ({position.year})" if position.year else ""
            sentence = _lead_sentence(by_marker[marker].content)
            parts.append(_cite(f"One position{year} holds that {sentence}", marker))
        return GenerationOutput(text=" ".join(parts), ordered_chunks=chunks, mode="extractive")


def _approx_tokens(text: str) -> int:
    return max(1, round(len(text) / 4))


def _lead_sentence(text: str) -> str:
    match = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
    lead = match[0] if match else text
    return lead[:400].strip()


def _cite(sentence: str, marker: int) -> str:
    """Attach the marker *inside* the sentence's terminal punctuation
    ("... events [1]."): a marker after the full stop reads as the start of
    the next sentence to every sentence splitter downstream (the grounding
    verifier, the voice segmenter), which would credit each citation to the
    wrong sentence."""
    body = sentence.rstrip()
    if body and body[-1] in ".!?":
        return f"{body[:-1].rstrip()} [{marker}]{body[-1]}"
    return f"{body} [{marker}]."


_LLM_MODEL = "claude-sonnet-4-6"
_QUERY_TYPES: frozenset[str] = frozenset(
    ("therapy", "diagnosis", "prognosis", "etiology", "harm", "guideline_comparison", "other")
)
# Prompt versions used by each LLM method — recorded in traces.
LLM_PROMPTS: dict[str, int] = {
    "classify_query": 1,
    "decompose": 1,
    "grade_retrieval": 1,
    "rewrite_query": 1,
    "detect_contradiction": 1,
    "generate_answer": 1,
    # Voice mode swaps the generation prompt for the answer-first, no-markdown
    # variant (11E.1); every other node is shared with the text path.
    "voice_answer": 1,
}


def _numbered_passages(chunks: list[RetrievedChunk]) -> str:
    return "\n\n".join(
        f"[{i}] ({c.publication_date.year if c.publication_date else 'n.d.'}, "
        f"grade {c.evidence_grade or '?'}) {c.content}"
        for i, c in enumerate(chunks, start=1)
    )


class LLMReasoner:
    """Claude-backed reasoning. Every method falls back to the deterministic
    heuristic on any provider error (missing/placeholder key, timeout, parse
    failure), so the graph never fails and never silently produces nothing."""

    name = "llm"

    def __init__(self, *, voice: bool = False) -> None:
        self._fallback = HeuristicReasoner()
        self.usage = UsageAccumulator()
        self._generation_prompt = "voice_answer" if voice else "generate_answer"

    async def _complete(self, prompt_name: str, user: str, *, max_tokens: int = 1024) -> str:
        from anthropic import AsyncAnthropic

        from app.config import get_settings
        from app.core.telemetry import span
        from app.prompts.loader import load_prompt

        prompt = load_prompt(prompt_name, LLM_PROMPTS[prompt_name])
        client = AsyncAnthropic(api_key=get_settings().anthropic_api_key.get_secret_value())
        with span(
            "provider.anthropic.messages",
            model=_LLM_MODEL,
            prompt=f"{prompt_name}.v{prompt.version}",
            max_tokens=max_tokens,
        ) as current:
            response = await client.messages.create(
                model=_LLM_MODEL,
                max_tokens=max_tokens,
                temperature=0.0,
                system=prompt.text,
                messages=[{"role": "user", "content": user}],
            )
            current.set_attribute("llm.input_tokens", response.usage.input_tokens)
            current.set_attribute("llm.output_tokens", response.usage.output_tokens)
        self.usage.add(_LLM_MODEL, response.usage.input_tokens, response.usage.output_tokens)
        cost.record(
            "generation",
            provider="anthropic",
            model=_LLM_MODEL,
            units=response.usage.input_tokens,
            unit="tokens",
            output_units=response.usage.output_tokens,
        )
        return "".join(b.text for b in response.content if b.type == "text").strip()

    async def classify(self, query: str) -> tuple[QueryType, bool]:
        try:
            raw = await self._complete("classify_query", query, max_tokens=20)
            type_part, _, multi = raw.lower().partition("|")
            query_type = type_part.strip()
            if query_type not in _QUERY_TYPES:
                raise ValueError(f"unknown type {query_type!r}")
            multi_hop = "yes" in multi or "true" in multi or "multi" in multi
            return cast(QueryType, query_type), multi_hop
        except Exception as exc:
            logger.warning("classify_llm_fallback", error=f"{type(exc).__name__}: {exc}")
            return await self._fallback.classify(query)

    async def decompose(self, query: str) -> list[str]:
        try:
            raw = await self._complete("decompose", query, max_tokens=300)
            subs = [line.strip(" -•\t") for line in raw.splitlines() if line.strip()]
            return subs or [query]
        except Exception as exc:
            logger.warning("decompose_llm_fallback", error=f"{type(exc).__name__}: {exc}")
            return await self._fallback.decompose(query)

    async def grade_retrieval(self, query: str, chunks: list[RetrievedChunk]) -> RetrievalGrade:
        try:
            user = f"QUESTION: {query}\n\nPASSAGES:\n{_numbered_passages(chunks[:8])}"
            raw = (await self._complete("grade_retrieval", user, max_tokens=12)).lower()
            for grade in ("insufficient", "irrelevant", "sufficient"):
                if grade in raw:
                    return grade
            raise ValueError(f"unparseable grade {raw!r}")
        except Exception as exc:
            logger.warning("grade_llm_fallback", error=f"{type(exc).__name__}: {exc}")
            return await self._fallback.grade_retrieval(query, chunks)

    async def rewrite_query(self, query: str, attempt: int, chunks: list[RetrievedChunk]) -> str:
        try:
            user = f"ATTEMPT: {attempt}\nORIGINAL QUESTION: {query}"
            rewritten = await self._complete("rewrite_query", user, max_tokens=120)
            return rewritten.splitlines()[0].strip() if rewritten else query
        except Exception as exc:
            logger.warning("rewrite_llm_fallback", error=f"{type(exc).__name__}: {exc}")
            return await self._fallback.rewrite_query(query, attempt, chunks)

    async def detect_contradiction(self, query: str, chunks: list[RetrievedChunk]) -> Contradiction:
        import json

        try:
            user = f"QUESTION: {query}\n\nPASSAGES:\n{_numbered_passages(chunks)}"
            raw = await self._complete("detect_contradiction", user, max_tokens=600)
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                raise ValueError("no JSON in contradiction response")
            payload = json.loads(match.group())
            if not payload.get("detected"):
                return Contradiction(detected=False, axis="none")
            positions = [
                ContradictionPosition(
                    stance=str(p["stance"]),
                    markers=[int(m) for m in p.get("markers", [])],
                    year=p.get("year"),
                )
                for p in payload.get("positions", [])
            ]
            return Contradiction(
                detected=True,
                positions=positions,
                axis=payload.get("axis", "unclear"),
                explanation=str(payload.get("explanation", "")),
            )
        except Exception as exc:
            logger.warning("contradiction_llm_fallback", error=f"{type(exc).__name__}: {exc}")
            return await self._fallback.detect_contradiction(query, chunks)

    async def generate(
        self, query: str, chunks: list[RetrievedChunk], contradiction: Contradiction
    ) -> GenerationOutput:
        try:
            conflict_note = (
                f"\n\nA contradiction was detected — structure the answer as a conflict, "
                f"never as false consensus. {contradiction.explanation}"
                if contradiction.detected
                else ""
            )
            user = (
                f"QUESTION: {query}\n\nNUMBERED PASSAGES:\n{_numbered_passages(chunks)}"
                f"{conflict_note}"
            )
            text = await self._complete(self._generation_prompt, user, max_tokens=1024)
            if not text:
                raise ValueError("empty generation")
            return GenerationOutput(text=text, ordered_chunks=chunks, mode="llm")
        except Exception as exc:
            logger.warning("generate_llm_fallback", error=f"{type(exc).__name__}: {exc}")
            return await self._fallback.generate(query, chunks, contradiction)

    async def generate_stream(
        self, query: str, chunks: list[RetrievedChunk], contradiction: Contradiction
    ) -> AsyncIterator[str | GenerationOutput]:
        """Token-streamed generation (Claude ``messages.stream``). Falls back
        to the heuristic stream on any provider error, so the voice pipeline
        keeps speaking. A cancelled stream stops the meter (11D.4)."""
        from anthropic import AsyncAnthropic

        from app.config import get_settings
        from app.prompts.loader import load_prompt

        conflict_note = (
            f"\n\nA contradiction was detected — structure the answer as a conflict, "
            f"never as false consensus. {contradiction.explanation}"
            if contradiction.detected
            else ""
        )
        user = (
            f"QUESTION: {query}\n\nNUMBERED PASSAGES:\n{_numbered_passages(chunks)}{conflict_note}"
        )
        pieces: list[str] = []
        try:
            prompt = load_prompt(self._generation_prompt, LLM_PROMPTS[self._generation_prompt])
            client = AsyncAnthropic(api_key=get_settings().anthropic_api_key.get_secret_value())
            async with client.messages.stream(
                model=_LLM_MODEL,
                max_tokens=1024,
                temperature=0.0,
                system=prompt.text,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                async for delta in stream.text_stream:
                    pieces.append(delta)
                    yield delta
                message = await stream.get_final_message()
            self.usage.add(_LLM_MODEL, message.usage.input_tokens, message.usage.output_tokens)
            text = "".join(pieces).strip()
            if not text:
                raise ValueError("empty generation")
            yield GenerationOutput(text=text, ordered_chunks=chunks, mode="llm")
        except Exception as exc:
            if pieces:
                # Tokens already spoken cannot be unsaid; keep them as the answer.
                logger.warning("generate_stream_partial", error=f"{type(exc).__name__}: {exc}")
                yield GenerationOutput(
                    text="".join(pieces).strip(), ordered_chunks=chunks, mode="llm"
                )
                return
            logger.warning("generate_stream_fallback", error=f"{type(exc).__name__}: {exc}")
            async for item in self._fallback.generate_stream(query, chunks, contradiction):
                yield item


class FreeLLMReasoner(HeuristicReasoner):
    """A free-tier model writes the answer; everything else stays offline.

    The graph asks a reasoner six things per question. On a free tier with
    30 requests and 8,000 tokens a minute, spending six calls per question
    would exhaust the minute on the first one, so classification, grading,
    rewriting and contradiction detection keep their deterministic forms —
    they decide from retrieval scores and passage metadata, which is what
    the offline engine does already — and the one call goes to the step
    where a model changes the result: turning passages into prose. Any
    provider failure falls back to extraction, so the graph never fails."""

    name = "free-llm"

    def __init__(self, *, voice: bool = False) -> None:
        super().__init__()
        self._generation_prompt = "voice_answer" if voice else "generate_answer"

    def _messages(
        self, query: str, chunks: list[RetrievedChunk], contradiction: Contradiction
    ) -> list[ChatMessage]:
        from app.prompts.loader import load_prompt

        prompt = load_prompt(self._generation_prompt, LLM_PROMPTS[self._generation_prompt])
        conflict_note = (
            f"\n\nA contradiction was detected — structure the answer as a conflict, "
            f"never as false consensus. {contradiction.explanation}"
            if contradiction.detected
            else ""
        )
        return [
            ChatMessage("system", prompt.text),
            ChatMessage(
                "user",
                f"QUESTION: {query}\n\nNUMBERED PASSAGES:\n"
                f"{_numbered_passages(chunks)}{conflict_note}",
            ),
        ]

    async def generate(
        self, query: str, chunks: list[RetrievedChunk], contradiction: Contradiction
    ) -> GenerationOutput:
        if not chunks:
            return await super().generate(query, chunks, contradiction)
        try:
            result = await ChatModel().complete(
                self._messages(query, chunks, contradiction), max_tokens=900
            )
            self.usage.add(result.model, result.input_tokens, result.output_tokens)
            return GenerationOutput(text=result.text, ordered_chunks=chunks, mode="llm")
        except (LLMUnavailable, ValueError) as exc:
            logger.warning("generate_free_llm_fallback", error=f"{type(exc).__name__}: {exc}")
            return await super().generate(query, chunks, contradiction)

    async def generate_stream(
        self, query: str, chunks: list[RetrievedChunk], contradiction: Contradiction
    ) -> AsyncIterator[str | GenerationOutput]:
        if not chunks:
            async for offline in super().generate_stream(query, chunks, contradiction):
                yield offline
            return
        spoken = False
        try:
            async for piece in ChatModel().stream(
                self._messages(query, chunks, contradiction), max_tokens=700
            ):
                if isinstance(piece, ChatResult):
                    self.usage.add(piece.model, piece.input_tokens, piece.output_tokens)
                    yield GenerationOutput(text=piece.text, ordered_chunks=chunks, mode="llm")
                    return
                spoken = True
                yield piece
        except LLMUnavailable as exc:
            if spoken:
                raise
            logger.warning("generate_free_llm_fallback", error=str(exc))
        async for fallback in super().generate_stream(query, chunks, contradiction):
            yield fallback


def get_reasoner(name: str, *, voice: bool = False) -> Reasoner:
    if name in ("heuristic", "offline"):
        return HeuristicReasoner()
    if name in ("llm", "claude", "anthropic"):
        return LLMReasoner(voice=voice)
    if name == "free-llm":
        return FreeLLMReasoner(voice=voice)
    raise ValueError(f"unknown reasoner: {name!r}")
