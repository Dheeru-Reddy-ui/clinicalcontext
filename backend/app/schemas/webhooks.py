"""Webhook schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# The events a tenant can subscribe to. Kept explicit so a typo in a
# subscription fails loudly at registration instead of silently never firing.
WebhookEvent = Literal[
    "query.completed",
    "batch.completed",
    "answer.superseded",
]


class WebhookCreateRequest(BaseModel):
    url: str = Field(max_length=2000)
    events: list[WebhookEvent] = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        # Signed payloads must not be sent over a channel anyone can read.
        if not value.startswith("https://"):
            raise ValueError("webhook url must be https://")
        return value


class WebhookOut(BaseModel):
    id: UUID
    url: str
    events: list[str]
    enabled: bool
    created_at: datetime


class WebhookCreatedOut(WebhookOut):
    # The signing secret is returned exactly once, at registration.
    secret: str


class WebhooksOut(BaseModel):
    webhooks: list[WebhookOut]


class DeliveryOut(BaseModel):
    id: UUID
    webhook_id: UUID
    event: str
    status: str
    attempts: int
    response_code: int | None
    last_error: str | None
    next_attempt_at: datetime | None
    created_at: datetime
    delivered_at: datetime | None
    payload: dict[str, Any]


class DeliveriesOut(BaseModel):
    deliveries: list[DeliveryOut]
    total: int
