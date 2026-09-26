"""Learn's tools: the AI tutor's quizzes, cases and progress; the clinical note
summarizer; Ask-this-Paper."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.answer import Citation
from app.schemas.assistant import ChatSessionOut

Level = Literal["mbbs", "pg"]
TutorMode = Literal["quiz", "case"]

# -- the AI tutor ------------------------------------------------------------------------


class QuizRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=120)
    specialty: str | None = Field(default=None, max_length=64)
    level: Level = "mbbs"
    count: int = Field(default=5, ge=1, le=10)
    mode: TutorMode = "quiz"


class QuizQuestionOut(BaseModel):
    stem: str
    options: list[str]
    # Index into options of the best answer. Sent with the question: this is
    # self-study, and the page reveals it only after an answer is chosen.
    answer: int
    explanation: str
    # Markers into QuizOut.sources (1-based) the explanation relies on.
    sources: list[int]
    stage: str | None = None


class QuizOut(BaseModel):
    mode: TutorMode
    topic: str
    level: Level
    specialty: str | None
    case: str | None
    questions: list[QuizQuestionOut]
    sources: list[Citation]
    generated_by: Literal["llm", "offline"]
    model: str | None
    dropped: int
    notices: list[str]


class AttemptIn(BaseModel):
    """One answered question, for the learner's own progress."""

    mode: TutorMode
    level: Level
    specialty: str | None = Field(default=None, pattern=r"^[a-z0-9-]{2,64}$")
    topic: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=3000)
    chosen: str = Field(min_length=1, max_length=600)
    answer: str = Field(min_length=1, max_length=600)
    explanation: str = Field(default="", max_length=4000)
    correct: bool


class AttemptOut(BaseModel):
    id: UUID


class TopicProgressOut(BaseModel):
    topic: str
    specialty: str | None
    answered: int
    correct: int
    accuracy: float
    last_at: datetime


class MistakeOut(BaseModel):
    topic: str
    question: str
    chosen: str
    answer: str
    explanation: str
    created_at: datetime


class ProgressOut(BaseModel):
    answered: int
    correct: int
    accuracy: float | None
    week_answered: int
    week_correct: int
    streak_days: int
    topics: list[TopicProgressOut]
    weakest: list[TopicProgressOut]
    mistakes: list[MistakeOut]


class ProgressClearedOut(BaseModel):
    deleted: int


# -- the clinical note summarizer ----------------------------------------------------------


class NoteSummarizeIn(BaseModel):
    text: str = Field(min_length=20, max_length=15_000)


class SummaryPointOut(BaseModel):
    text: str
    # The note's line numbers (1-based) the point comes from.
    lines: list[int]
    support: Literal["supported", "partially_supported", "quoted"]


class SummarySectionOut(BaseModel):
    heading: str
    points: list[SummaryPointOut]


class NoteSummaryOut(BaseModel):
    sections: list[SummarySectionOut]
    # The note as it was read — identifiers already replaced — numbered from 1.
    lines: list[str]
    # What was taken out, by kind (counts only).
    redactions: dict[str, int]
    mode: Literal["llm", "extractive"]
    model: str | None
    removed: int
    notice: str | None


class NoteTextOut(BaseModel):
    """Text read from an uploaded report, for the person to check before it
    is summarized. Nothing is stored."""

    text: str
    pages: int | None
    characters: int
    truncated: bool


# -- Ask-this-Paper -------------------------------------------------------------------------


class PaperOut(BaseModel):
    id: UUID
    title: str
    pages: int | None
    chunk_count: int
    study_type: str | None
    evidence_grade: str | None
    uploaded_at: datetime
    uploaded_by_you: bool
    deduplicated: bool = False


class PaperSectionOut(BaseModel):
    title: str
    page: int | None


class PaperDetail(PaperOut):
    abstract: str | None
    outline: list[PaperSectionOut]
    conversations: list[ChatSessionOut]


class PaperDeletedOut(BaseModel):
    deleted: bool
    conversations_deleted: int
    conversations_kept: int
