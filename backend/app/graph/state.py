"""Agent-graph state and streamed-event types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from app.retrieval.types import RetrievedChunk
from app.schemas.answer import (
    Citation,
    Confidence,
    Contradiction,
    EvidenceGrade,
    QueryType,
    RetrievalGrade,
)


class GraphState(TypedDict, total=False):
    """LangGraph state. Nodes return partial dicts that are merged in.

    Held in memory for the run (no checkpointer), so RetrievedChunk objects
    can live here directly.
    """

    query: str
    query_type: QueryType
    is_multi_hop: bool
    sub_questions: list[str]
    current_query: str
    per_subquestion: dict[str, list[str]]  # sub-question -> retrieved chunk id strings
    retrieved: list[RetrievedChunk]
    retrieval_grade: RetrievalGrade
    rewrite_count: int
    contradiction: Contradiction
    answer: str
    citations: list[Citation]
    ordered_chunks: list[RetrievedChunk]
    generation_mode: str
    abstained: bool
    # The answer was withheld by the grounding check, not for thin evidence.
    grounding_rejected: bool
    confidence: Confidence
    evidence_grade: EvidenceGrade | None
    prompt_versions: dict[str, str]
    model: str


@dataclass(slots=True)
class GraphEvent:
    """A streamed reasoning-progress event (SSE payload)."""

    stage: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "message": self.message, "data": self.data}
