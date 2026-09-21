"""Session schemas — the unit of multi-turn conversation and of history."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class SessionOut(BaseModel):
    id: UUID
    title: str | None
    query_count: int
    created_at: datetime
    last_query_at: datetime | None


class SessionsOut(BaseModel):
    sessions: list[SessionOut]
    total: int


class SessionTurn(BaseModel):
    """One turn: what was asked, what it was resolved to, and what came back."""

    query_id: UUID
    raw_query: str
    contextualized_query: str | None
    status: str
    created_at: datetime
    answer_id: UUID | None
    answer: str | None
    confidence: str | None
    abstained: bool | None


class SessionDetailOut(BaseModel):
    session: SessionOut
    turns: list[SessionTurn]
