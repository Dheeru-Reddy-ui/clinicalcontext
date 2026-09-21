"""Answer-domain schemas: the structured output of the agent graph.

These are transport/domain types, independent of LangGraph, so they can be
returned by the API, persisted to the ``answers`` table, and asserted in
tests without importing graph internals.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

QueryType = Literal[
    "therapy", "diagnosis", "prognosis", "etiology", "harm", "guideline_comparison", "other"
]
Confidence = Literal["high", "moderate", "low"]
EvidenceGrade = Literal["A", "B", "C", "D"]
RetrievalGrade = Literal["sufficient", "insufficient", "irrelevant"]


class Citation(BaseModel):
    """A source backing an inline ``[n]`` marker in the answer."""

    marker: int
    chunk_id: UUID
    document_id: UUID
    title: str | None
    section: str | None
    publication_date: str | None  # ISO date; str for JSON/trace friendliness
    evidence_grade: EvidenceGrade | None
    study_type: str | None
    journal: str | None = None
    pmid: str | None = None
    doi: str | None = None
    url: str | None = None
    passage: str


class ContradictionPosition(BaseModel):
    """One side of a detected disagreement."""

    stance: str
    markers: list[int]
    year: int | None = None


class Contradiction(BaseModel):
    """A first-class disagreement among the retrieved passages."""

    detected: bool
    positions: list[ContradictionPosition] = Field(default_factory=list)
    # Why they differ: 'temporal' (different years), 'population', 'endpoint', 'unclear'.
    axis: Literal["temporal", "population", "endpoint", "unclear", "none"] = "none"
    explanation: str = ""


class SubQuestionTrace(BaseModel):
    question: str
    retrieved_chunk_ids: list[UUID] = Field(default_factory=list)


class AnswerResult(BaseModel):
    """The complete result of one agent-graph run."""

    query: str
    query_type: QueryType
    is_multi_hop: bool
    sub_questions: list[str] = Field(default_factory=list)
    abstained: bool
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    contradiction: Contradiction = Field(default_factory=lambda: Contradiction(detected=False))
    confidence: Confidence
    evidence_grade: EvidenceGrade | None
    retrieval_grade: RetrievalGrade
    rewrite_count: int = 0
    escalation_banner: str | None = None
    # generation provenance
    model: str = "heuristic"
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    generation_mode: Literal["llm", "extractive"] = "extractive"
    # The LangSmith run this answer was produced in (None when tracing is off).
    langsmith_run_id: str | None = None
