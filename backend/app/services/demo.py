"""The public demo (Phase 13): one real query, no login, from the marketing page.

The demo is the real pipeline — guardrails, retrieval, contradiction
detection, grounding — run as the fixed demo tenant (migration 022) on a
question from a short allowlist. The allowlist is the only input an
anonymous visitor controls: free text would make the marketing page an
unauthenticated query endpoint. The response is the answer as the product
would show it, including a detected contradiction when there is one; which
questions surface a contradiction is a property of the corpus at the time,
so the allowlist notes what each question *tends* to show rather than
promising it.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from app.core.deps import DbPool
from app.schemas.demo import DemoAnswerOut, DemoQuestion
from app.services.ask import AskService

DEMO_ORG_ID = UUID("00000000-0000-4000-8000-000000000d30")
DEMO_USER_ID = UUID("00000000-0000-4000-8000-000000000d31")

# The questions a visitor can pick from. `shows` is what the question tended
# to surface on the seeded corpus when the list was written — a hint for the
# page, not a promise; the page renders whatever the pipeline returns.
DEMO_QUESTIONS: list[DemoQuestion] = [
    DemoQuestion(
        id="beta-blockers-post-mi",
        question="Should beta-blockers be used after myocardial infarction?",
        shows="contradiction",
        why="The retrieved sources reach opposing conclusions; the answer shows both sides.",
    ),
    DemoQuestion(
        id="vitamin-d-fractures",
        question="Does vitamin D supplementation prevent fractures?",
        shows="contradiction",
        why="Sources disagree; the answer says so instead of picking one.",
    ),
    DemoQuestion(
        id="aspirin-primary-prevention",
        question="Should aspirin be used for primary prevention of cardiovascular disease?",
        shows="cited answer",
        why="A cited, graded answer with every claim traceable to a source.",
    ),
    DemoQuestion(
        id="metformin-first-line",
        question="Is metformin first-line therapy for type 2 diabetes?",
        shows="cited answer",
        why="A high-confidence answer — and how often that label is right is published.",
    ),
]
_BY_ID = {q.id: q for q in DEMO_QUESTIONS}


def demo_question(question_id: str) -> DemoQuestion | None:
    return _BY_ID.get(question_id)


class DemoService:
    def __init__(self, pool: DbPool, redis: Any) -> None:
        self._pool = pool
        self._redis = redis

    async def ask(self, question: DemoQuestion) -> DemoAnswerOut:
        started = time.perf_counter()
        result: dict[str, Any] | None = None
        blocked: dict[str, Any] | None = None
        async for event in AskService(self._pool, self._redis).ask(
            query=question.question, org_id=DEMO_ORG_ID, user_id=DEMO_USER_ID
        ):
            if event["stage"] == "result":
                result = event["data"]
            elif event["stage"] == "blocked":
                blocked = event
        latency_ms = int((time.perf_counter() - started) * 1000)
        if result is None:
            # The allowlisted questions are clinical literature questions; a
            # block here would be a guardrail regression, reported as such.
            message = blocked["message"] if blocked else "no answer was produced"
            return DemoAnswerOut(
                question_id=question.id,
                question=question.question,
                answer="",
                abstained=True,
                blocked=message,
                latency_ms=latency_ms,
            )
        return DemoAnswerOut(
            question_id=question.id,
            question=question.question,
            answer=result["answer"],
            abstained=bool(result["abstained"]),
            confidence=result.get("confidence"),
            evidence_grade=result.get("evidence_grade"),
            contradiction=result["contradiction"],
            citations=[_public_citation(c) for c in result["citations"]],
            cached=bool(result.get("cached", False)),
            generation_mode=result.get("generation_mode", "extractive"),
            model=result.get("model", ""),
            latency_ms=latency_ms,
        )


def _public_citation(citation: dict[str, Any]) -> dict[str, Any]:
    """What a visitor needs to check a source — no internal ids."""
    return {
        key: citation.get(key)
        for key in (
            "marker",
            "title",
            "section",
            "publication_date",
            "evidence_grade",
            "study_type",
            "journal",
            "pmid",
            "doi",
            "url",
            "passage",
        )
    }
