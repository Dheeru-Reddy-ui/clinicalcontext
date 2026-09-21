"""API-key schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ApiKeyScope = Literal["read", "query", "full"]


def _default_scopes() -> list[ApiKeyScope]:
    return ["query"]


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[ApiKeyScope] = Field(default_factory=_default_scopes, min_length=1)


class ApiKeyOut(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreatedOut(ApiKeyOut):
    # The full key is returned exactly once, at creation.
    key: str


class ApiKeysOut(BaseModel):
    api_keys: list[ApiKeyOut]
