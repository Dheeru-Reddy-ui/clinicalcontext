"""LLM-scored generation metrics for the golden set.

Two layers, both behind the ``AnswerJudge`` protocol so the harness can be
exercised with a scripted judge and a real key can be dropped in later:

* The four RAGAS metrics (Es et al., 2023), computed the way the paper
  defines them rather than through the ``ragas`` package (which needs a
  LangChain-wrapped model): **faithfulness** — claims in the answer supported
  by the cited passages; **answer relevance** — similarity between the
  question and questions the answer would answer, in the configured embedding
  space; **context precision** — rank-weighted usefulness of retrieved
  passages; **context recall** — reference-answer sentences attributable to
  the retrieved passages.
* A clinical rubric (1 to 5): clinical accuracy against the reference, citation
  correctness, appropriate hedging, appropriate abstention. An answer is
  *correct* for calibration when clinical accuracy is 4 or 5.

Without a usable ``ANTHROPIC_API_KEY`` the judge does not run and the report
says so (``ran: false``) — no score is invented.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Protocol

import structlog
from pydantic import BaseModel

from app.config import get_settings
from app.guardrails.grounding import split_sentences
from app.prompts.loader import load_prompt
from app.retrieval.embedders import TextEmbedder

logger = structlog.stdlib.get_logger("evals.golden.judge")

MODEL = "claude-sonnet-4-6"
PROMPTS = {"golden_faithfulness": 1, "golden_relevance": 1, "golden_rubric": 1}
RUBRIC = (
    "clinical_accuracy",
    "citation_correctness",
    "appropriate_hedging",
    "appropriate_abstention",
)
CORRECT_AT = 4


class JudgeInput(BaseModel):
    item_id: str
    question: str
    reference_answer: str
    expected_abstain: bool
    answer: str
    abstained: bool
    cited_passages: list[str]
    retrieved_passages: list[str]


class JudgeScores(BaseModel):
    faithfulness: float | None
    answer_relevance: float | None
    context_precision: float | None
    context_recall: float | None
    clinical_accuracy: int
    citation_correctness: int
    appropriate_hedging: int
    appropriate_abstention: int
    correct: bool
    notes: str = ""


class AnswerJudge(Protocol):
    name: str

    async def score(self, item: JudgeInput) -> JudgeScores: ...


def key_usable() -> bool:
    key = get_settings().anthropic_api_key.get_secret_value()
    return bool(key) and not key.startswith(("placeholder", "test-", "your-"))


def _json_object(raw: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"judge returned no JSON: {raw[:120]!r}")
    payload: dict[str, Any] = json.loads(match.group())
    return payload


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def context_precision(useful: list[bool]) -> float | None:
    """RAGAS context precision: mean of precision@k over the useful ranks."""
    if not useful:
        return None
    relevant = sum(1 for u in useful if u)
    if relevant == 0:
        return 0.0
    total = 0.0
    seen = 0
    for rank, flag in enumerate(useful, start=1):
        if flag:
            seen += 1
            total += seen / rank
    return round(total / relevant, 4)


class AnthropicJudge:
    """The real judge: three calls per item (claims, questions, rubric)."""

    name = MODEL

    def __init__(self, embedder: TextEmbedder) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=get_settings().anthropic_api_key.get_secret_value())
        self._embedder = embedder

    async def _ask(self, prompt_name: str, user: str, *, max_tokens: int) -> dict[str, Any]:
        prompt = load_prompt(prompt_name, PROMPTS[prompt_name])
        response = await self._client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            temperature=0.0,
            system=prompt.text,
            messages=[{"role": "user", "content": user}],
        )
        return _json_object("".join(b.text for b in response.content if b.type == "text"))

    async def score(self, item: JudgeInput) -> JudgeScores:
        numbered = "\n".join(f"[{i}] {p}" for i, p in enumerate(item.cited_passages, start=1))
        retrieved = "\n".join(f"[{i}] {p}" for i, p in enumerate(item.retrieved_passages, start=1))

        faithfulness: float | None = None
        if item.answer.strip() and not item.abstained:
            claims = await self._ask(
                "golden_faithfulness",
                f"ANSWER:\n{item.answer}\n\nCITED PASSAGES:\n{numbered or '(none)'}",
                max_tokens=1200,
            )
            verdicts = [str(c.get("verdict", "unsupported")) for c in claims.get("claims", [])]
            if verdicts:
                credit = {"supported": 1.0, "partial": 0.5, "unsupported": 0.0}
                faithfulness = round(sum(credit.get(v, 0.0) for v in verdicts) / len(verdicts), 4)

        answer_relevance: float | None = None
        if item.answer.strip():
            generated = await self._ask(
                "golden_relevance", f"ANSWER:\n{item.answer}", max_tokens=400
            )
            questions = [str(q) for q in generated.get("questions", []) if str(q).strip()]
            if questions:
                vectors = await self._embedder.embed_queries([item.question, *questions])
                sims = [_cosine(vectors[0], v) for v in vectors[1:]]
                answer_relevance = round(sum(sims) / len(sims), 4)

        rubric = await self._ask(
            "golden_rubric",
            "\n\n".join(
                [
                    f"QUESTION: {item.question}",
                    f"REFERENCE ANSWER: {item.reference_answer}",
                    f"ABSTENTION WAS EXPECTED: {'yes' if item.expected_abstain else 'no'}",
                    f"ANSWER{' (abstained)' if item.abstained else ''}:\n{item.answer}",
                    f"CITED PASSAGES:\n{numbered or '(none)'}",
                    f"RETRIEVED PASSAGES:\n{retrieved or '(none)'}",
                ]
            ),
            max_tokens=600,
        )
        useful = [bool(u) for u in rubric.get("useful", [])][: len(item.retrieved_passages)]
        attributable = [bool(a) for a in rubric.get("attributable", [])]
        sentences = split_sentences(item.reference_answer)
        recall = (
            round(sum(1 for a in attributable[: len(sentences)] if a) / len(sentences), 4)
            if sentences and attributable
            else None
        )
        scores = {c: max(1, min(5, int(rubric.get(c, 1)))) for c in RUBRIC}
        return JudgeScores(
            faithfulness=faithfulness,
            answer_relevance=answer_relevance,
            context_precision=context_precision(useful),
            context_recall=recall,
            correct=scores["clinical_accuracy"] >= CORRECT_AT,
            notes=str(rubric.get("notes", "")),
            **scores,
        )


def resolve_judge(embedder: TextEmbedder, *, disabled: bool) -> AnswerJudge | None:
    if disabled or not key_usable():
        return None
    return AnthropicJudge(embedder)


def not_run_reason(*, disabled: bool) -> str:
    if disabled:
        return "--no-judge"
    return "ANTHROPIC_API_KEY is a placeholder — RAGAS and the rubric need a real key"


async def judge_items(judge: AnswerJudge, inputs: list[JudgeInput]) -> dict[str, Any]:
    scored: dict[str, JudgeScores] = {}
    for entry in inputs:
        try:
            scored[entry.item_id] = await judge.score(entry)
        except Exception as exc:
            logger.warning("judge_failed", item=entry.item_id, error=type(exc).__name__)
    return {
        "ran": True,
        "model": judge.name,
        "prompt_versions": {k: f"{k}.v{v}" for k, v in PROMPTS.items()},
        "scored": len(scored),
        "means": _means(list(scored.values())),
        "per_item": {k: v.model_dump() for k, v in scored.items()},
    }


def _means(scores: list[JudgeScores]) -> dict[str, float | None]:
    def avg(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    return {
        "faithfulness": avg([s.faithfulness for s in scores if s.faithfulness is not None]),
        "answer_relevance": avg(
            [s.answer_relevance for s in scores if s.answer_relevance is not None]
        ),
        "context_precision": avg(
            [s.context_precision for s in scores if s.context_precision is not None]
        ),
        "context_recall": avg([s.context_recall for s in scores if s.context_recall is not None]),
        **{c: avg([float(getattr(s, c)) for s in scores]) for c in RUBRIC},
        "correct_rate": avg([1.0 if s.correct else 0.0 for s in scores]),
    }


__all__ = [
    "CORRECT_AT",
    "MODEL",
    "PROMPTS",
    "RUBRIC",
    "AnswerJudge",
    "AnthropicJudge",
    "JudgeInput",
    "JudgeScores",
    "context_precision",
    "judge_items",
    "key_usable",
    "not_run_reason",
    "resolve_judge",
]
