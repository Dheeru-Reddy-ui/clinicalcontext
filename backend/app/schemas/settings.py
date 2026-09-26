"""Settings: a person's profile, their preferences, and their own data."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.voice.voices import DEFAULT_VOICE

Audience = Literal["patient", "clinician", "student"]
LearnScope = Literal["all", "mbbs", "pg"]
LearnDepth = Literal["auto", "mbbs", "pg"]

#: The assistant conversations a person may delete. Evidence searches ("ask")
#: are the organization's record — History and the dashboard read them — so
#: they are kept.
DELETABLE_KINDS: tuple[str, ...] = ("chat", "learn", "treatment", "voice", "tutor", "paper")


class PreferencesOut(BaseModel):
    """What the assistant, voice and Learn do by default for this person.

    ``saved`` is False until the person first changes something: until then
    these are the product's defaults."""

    audience: Audience = "patient"
    sources_open: bool = False
    show_timeline: bool = True
    voice_name: str = DEFAULT_VOICE
    voice_rate: float = 1.0
    voice_continuous: bool = True
    learn_scope: LearnScope = "all"
    learn_depth: LearnDepth = "auto"
    followed_specialties: list[str] = Field(default_factory=list)
    saved: bool = False
    updated_at: datetime | None = None


class PreferencesUpdate(BaseModel):
    """Only the fields sent change; send ``[]`` to clear followed specialties."""

    model_config = ConfigDict(extra="forbid")

    audience: Audience | None = None
    sources_open: bool | None = None
    show_timeline: bool | None = None
    voice_name: str | None = Field(default=None, max_length=20)
    voice_rate: float | None = Field(default=None, ge=0.75, le=1.5)
    voice_continuous: bool | None = None
    learn_scope: LearnScope | None = None
    learn_depth: LearnDepth | None = None
    followed_specialties: list[str] | None = Field(default=None, max_length=60)


class ProfileUpdate(BaseModel):
    """A person's own name and specialty. An empty string clears the field."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    full_name: str | None = Field(default=None, max_length=120)
    specialty: str | None = Field(default=None, max_length=120)


class ConversationsDeletedOut(BaseModel):
    deleted: int
    # Conversations left in place because an answer in them is in a binder,
    # behind a public link, or has recorded versions (Living Answers).
    kept: int
