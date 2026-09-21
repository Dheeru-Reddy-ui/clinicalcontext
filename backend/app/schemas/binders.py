"""Evidence Binders: curated collections of answers and passages, annotated."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

BinderVisibility = Literal["private", "org"]
BinderItemType = Literal["answer", "passage"]


class BinderCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    visibility: BinderVisibility = "private"


class BinderUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    visibility: BinderVisibility | None = None


class BinderOut(BaseModel):
    id: UUID
    title: str
    description: str | None
    visibility: BinderVisibility
    created_by: UUID | None
    created_by_name: str | None
    item_count: int
    created_at: datetime


class BindersOut(BaseModel):
    binders: list[BinderOut]


class BinderItemCreateRequest(BaseModel):
    item_type: BinderItemType
    answer_id: UUID | None = None
    chunk_id: UUID | None = None

    @model_validator(mode="after")
    def _check_target(self) -> BinderItemCreateRequest:
        if self.item_type == "answer" and not self.answer_id:
            raise ValueError("answer items require answer_id")
        if self.item_type == "passage" and not self.chunk_id:
            raise ValueError("passage items require chunk_id")
        return self


class AnnotationCreateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    # Character offsets into the passage text: {"start": int, "end": int}.
    highlight_range: dict[str, int] | None = None
    parent_id: UUID | None = None


class AnnotationOut(BaseModel):
    id: UUID
    binder_item_id: UUID | None
    author_id: UUID | None
    author_name: str | None
    body: str
    highlight_range: dict[str, int] | None
    parent_id: UUID | None
    created_at: datetime
    replies: list[AnnotationOut] = Field(default_factory=list)


class AnswerSnapshot(BaseModel):
    """What a binder shows for a saved answer — enough to read it standalone."""

    id: UUID
    query: str
    content: str
    citations: list[dict[str, Any]]
    confidence: str | None
    evidence_grade: str | None
    has_contradiction: bool
    abstained: bool
    reasoning: dict[str, Any]
    created_at: datetime


class PassageSnapshot(BaseModel):
    chunk_id: UUID
    document_id: UUID
    document_title: str
    section: str | None
    content: str
    journal: str | None
    publication_date: str | None
    evidence_grade: str | None
    study_type: str | None
    pmid: str | None
    doi: str | None


class BinderItemOut(BaseModel):
    id: UUID
    item_type: BinderItemType
    position: int
    added_by: UUID | None
    added_by_name: str | None
    created_at: datetime
    answer: AnswerSnapshot | None = None
    passage: PassageSnapshot | None = None
    annotations: list[AnnotationOut] = Field(default_factory=list)


class BinderDetailOut(BaseModel):
    binder: BinderOut
    items: list[BinderItemOut]
