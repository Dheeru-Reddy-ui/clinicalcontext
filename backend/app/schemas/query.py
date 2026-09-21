"""Request schemas for the query endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

FeedbackReason = Literal["wrong", "unsupported", "outdated", "incomplete", "should_have_abstained"]


class PICO(BaseModel):
    """Structured Population / Intervention / Comparison / Outcome."""

    population: str | None = None
    intervention: str | None = None
    comparison: str | None = None
    outcome: str | None = None


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    session_id: UUID | None = None
    mode: Literal["standard", "comparison"] = "standard"
    # For comparison mode: the entities to compare (2-8).
    entities: list[str] = Field(default_factory=list, max_length=8)
    pico: PICO | None = None

    @model_validator(mode="after")
    def _check_comparison(self) -> QueryRequest:
        if self.mode == "comparison" and len(self.entities) < 2:
            raise ValueError("comparison mode requires at least 2 entities")
        return self


class BatchQueryRequest(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=50)


class FeedbackRequest(BaseModel):
    answer_id: UUID
    rating: Literal["up", "down"]
    reason: FeedbackReason | None = None
    comment: str | None = Field(default=None, max_length=2000)


class FeedbackOut(BaseModel):
    id: UUID
    answer_id: UUID
    rating: Literal["up", "down"]
    reason: FeedbackReason | None
    comment: str | None
    created_at: datetime


class ReviewQueueItem(BaseModel):
    """A thumbs-down (wrong / unsupported) awaiting review for the golden set."""

    id: UUID
    feedback_id: UUID
    answer_id: UUID
    query_id: UUID
    status: Literal["pending", "promoted", "rejected"]
    question: str
    answer_excerpt: str
    reason: FeedbackReason | None
    comment: str | None
    confidence: str | None
    citations: int
    golden_id: str | None
    created_at: datetime
    reviewed_at: datetime | None


class ReviewQueueOut(BaseModel):
    items: list[ReviewQueueItem]
    pending: int
    promoted: int
    rejected: int
