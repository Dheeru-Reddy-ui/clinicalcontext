"""The chat assistant, Learn and Treatment tabs: request and response shapes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.treatment.formulary import Profile

Audience = Literal["patient", "clinician", "student"]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    audience: Audience = "patient"
    session_id: UUID | None = None
    kind: Literal["chat", "learn", "treatment"] = "chat"
    # Learn: the specialty the conversation is about, and the level taught.
    specialty: str | None = Field(default=None, max_length=64)
    level: Literal["mbbs", "pg"] | None = None


class PublicTurn(BaseModel):
    question: str = Field(max_length=4000)
    answer: str = Field(max_length=6000)


class PublicChatRequest(BaseModel):
    """The website's chatbot: nothing is stored, so recent turns come along."""

    message: str = Field(min_length=1, max_length=2000)
    audience: Audience = "patient"
    history: list[PublicTurn] = Field(default_factory=list, max_length=4)


class ChatSessionOut(BaseModel):
    id: UUID
    title: str | None
    kind: str
    created_at: datetime
    updated_at: datetime
    turns: int


class ChatTurnOut(BaseModel):
    query_id: UUID
    question: str
    audience: str | None
    status: str
    created_at: datetime
    answer_id: UUID | None
    answer: str | None
    citations: list[dict[str, Any]]
    model: str | None
    details: dict[str, Any]


class ChatSessionDetail(BaseModel):
    id: UUID
    title: str | None
    kind: str
    created_at: datetime
    updated_at: datetime
    turns: list[ChatTurnOut]


class RenameSession(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class AssistantStatus(BaseModel):
    """What powers the assistant on this server, so the page can say so."""

    llm_available: bool
    providers: list[str]
    live_search: bool = True
    message: str


# -- Learn -----------------------------------------------------------------------------


class SpecialtyOut(BaseModel):
    slug: str
    name: str
    level: str
    level_label: str
    topics: list[str]


class FeedItemOut(BaseModel):
    pmid: str
    title: str
    journal: str
    published: str
    design: str
    url: str


class SpecialtyFeedOut(BaseModel):
    specialty: SpecialtyOut
    items: list[FeedItemOut]
    days: int
    available: bool = True
    message: str | None = None


# -- Treatment -------------------------------------------------------------------------


class ComplaintOut(BaseModel):
    id: str
    name: str
    summary: str


class OptionOut(BaseModel):
    id: str
    label: str


class QuestionOut(BaseModel):
    id: str
    text: str
    kind: Literal["single", "multi"]
    help: str | None
    options: list[OptionOut]


class SourceOut(BaseModel):
    key: str
    title: str
    publisher: str
    url: str


class ReasonOut(BaseModel):
    text: str
    urgency: str
    source: str | None


class MedicineOut(BaseModel):
    key: str
    name: str
    purpose: str
    suitable: bool
    dose: str | None
    how_often: str | None
    maximum: str | None
    notes: list[str]
    reason_not_suitable: str | None
    sources: list[str]


class DoctorOptionOut(BaseModel):
    text: str
    source: str


class AssessmentOut(BaseModel):
    urgency: Literal["self_care", "soon", "urgent", "emergency"]
    headline: str
    action: str
    reasons: list[ReasonOut]
    possible_causes: list[str]
    self_care: list[str]
    medicines: list[MedicineOut]
    doctor_may: list[DoctorOptionOut]
    tests: list[str]
    see_doctor_if: list[str]
    sources: list[SourceOut]


class TreatmentStepRequest(BaseModel):
    complaint: str = Field(max_length=64)
    profile: Profile
    answers: dict[str, list[str]] = Field(default_factory=dict)


class TreatmentStepOut(BaseModel):
    complaint: ComplaintOut
    question: QuestionOut | None = None
    assessment: AssessmentOut | None = None
    answered: int
    total: int
