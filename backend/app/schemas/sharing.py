"""Shareable read-only answer permalinks and the org-level sharing policy."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ShareLinkCreateRequest(BaseModel):
    answer_id: UUID


class ShareLinkOut(BaseModel):
    id: UUID
    answer_id: UUID
    slug: str
    enabled: bool
    # The app route the slug resolves to (the frontend renders /a/{slug}).
    path: str
    created_by: UUID | None
    created_at: datetime


class ShareLinksOut(BaseModel):
    links: list[ShareLinkOut]


class SharingPolicyOut(BaseModel):
    public_sharing_enabled: bool


class SharingPolicyUpdate(BaseModel):
    public_sharing_enabled: bool


class SupersededInfo(BaseModel):
    """Present when a Living Answer rerun produced a newer version."""

    latest_version: int
    superseded_at: datetime
    content: str
    citations: list[dict[str, Any]]
    confidence: str | None
    diff: dict[str, Any] | None


class PublicAnswerOut(BaseModel):
    """Everything a logged-out reader needs to verify the answer — nothing more.

    No user identity, no org internals beyond its display name, no query ids.
    """

    slug: str
    organization: str
    query: str
    content: str
    citations: list[dict[str, Any]]
    confidence: str | None
    evidence_grade: str | None
    has_contradiction: bool
    abstained: bool
    reasoning: dict[str, Any]
    model: str
    prompt_version: str
    answered_at: datetime
    superseded: SupersededInfo | None
