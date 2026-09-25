"""Voice REST schemas (the WebSocket protocol lives in ``app.voice.protocol``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

VoiceOutcome = Literal[
    "answered",
    "abstained",
    "blocked_phi",
    "blocked_scope",
    "confirm_requested",
    "cancelled",
    "error",
]
TtsQuality = Literal["flash", "multilingual"]


class VoiceConfigOut(BaseModel):
    backend: Literal["offline", "cloud"]
    stt_provider: str
    stt_model: str
    tts_provider: str
    tts_model: str
    tts_quality: TtsQuality
    sample_rate: int = 16_000
    ws_path: str = "/api/v1/voice/ws"
    boost_terms: int
    lexicon_entries: int
    lexicon_coverage: float
    lasa_pairs: int
    # False when this server cannot run voice at all (no engine, no key);
    # the page then says why instead of opening a session that dies.
    available: bool = True
    unavailable_reason: str | None = None


class VoiceSettingsIn(BaseModel):
    tts_quality: TtsQuality


class VoiceSettingsOut(BaseModel):
    tts_quality: TtsQuality


class VoiceChoiceOut(BaseModel):
    id: str
    label: str
    gender: Literal["female", "male"]
    accent: str
    tone: str


class VoicesOut(BaseModel):
    """The read-aloud voices Settings offers, and whether this server honours
    the choice (only the Deepgram speech service has one)."""

    provider: str
    selectable: bool
    default: str
    voices: list[VoiceChoiceOut]


class VoiceTurnOut(BaseModel):
    id: UUID
    voice_session_id: UUID
    query_session_id: UUID | None
    query_id: UUID | None
    turn_index: int
    backend: str
    stt_model: str
    tts_model: str
    transcript_raw: str
    transcript_final: str
    corrections: list[dict[str, Any]] = Field(default_factory=list)
    confirmation: dict[str, Any] = Field(default_factory=dict)
    outcome: VoiceOutcome
    blocked_by: str | None
    latency: dict[str, Any] = Field(default_factory=dict)
    speculation: dict[str, Any] = Field(default_factory=dict)
    barge_in: dict[str, Any] = Field(default_factory=dict)
    waste: dict[str, Any] = Field(default_factory=dict)
    mask_used: bool
    created_at: datetime


class VoiceTurnList(BaseModel):
    items: list[VoiceTurnOut]
    total: int


class LegStats(BaseModel):
    p50: float | None
    p95: float | None
    n: int


class VoiceAnalyticsOut(BaseModel):
    """Aggregates for the dashboard (11G.5, 11D.1, 11D.4, 11F.4)."""

    days: int
    turns: int
    answered: int
    blocked: int
    abstained: int
    legs: dict[str, LegStats]
    total_first_audio: LegStats
    client_first_audio: LegStats
    speculation_fired: int
    speculation_hits: int
    speculation_wasted: int
    speculation_hit_rate: float | None
    speculation_wasted_rate: float | None
    confirmations_requested: int
    confirmations_resolved_by_voice: int
    confirmations_resolved_by_tap: int
    barge_ins: int
    barge_in_stop_ms: LegStats
    masks_used: int
    wasted_output_tokens: int
    wasted_audio_ms: float
    corrections_made: int
    by_backend: dict[str, int]
