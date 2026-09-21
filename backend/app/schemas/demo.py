"""Schemas for the public demo query (Phase 13)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DemoQuestion(BaseModel):
    id: str
    question: str
    # What the question tended to surface on the seeded corpus; a hint for
    # the page, never a promise — the answer is whatever the pipeline returns.
    shows: str
    why: str


class DemoQuestionsOut(BaseModel):
    questions: list[DemoQuestion]


class DemoRequest(BaseModel):
    question_id: str = Field(min_length=1, max_length=64)


class DemoAnswerOut(BaseModel):
    question_id: str
    question: str
    answer: str
    abstained: bool
    confidence: str | None = None
    evidence_grade: str | None = None
    contradiction: dict[str, Any] = Field(default_factory=dict)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    cached: bool = False
    generation_mode: str = "extractive"
    model: str = ""
    latency_ms: int
    # Set only when a guardrail stopped the question (a regression, not a feature).
    blocked: str | None = None
