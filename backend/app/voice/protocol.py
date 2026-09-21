"""The wire protocol of ``/api/v1/voice/ws`` (11A.1).

One WebSocket per voice session carrying:

* **up**: binary frames of raw 16 kHz, 16-bit, mono PCM (20 ms = 640 bytes),
  plus JSON control messages (``ClientMessage``);
* **down**: JSON events (``ServerEvent``) multiplexed with binary TTS audio.

Downstream audio frames carry a 6-byte header so the client can discard audio
that belongs to a turn or sentence it has already flushed (barge-in):

    u8 kind (1 = pcm16 audio) | u16 turn | u16 sentence | u8 flags

Everything JSON is a Pydantic model: the frontend types mirror these and the
protocol test pins the shapes.
"""

from __future__ import annotations

import struct
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

SAMPLE_RATE = 16_000
FRAME_MS = 20
FRAME_BYTES = SAMPLE_RATE * 2 * FRAME_MS // 1000  # 640

AUDIO_KIND_PCM16 = 1
# Sentence index used for lines spoken outside a turn's answer pipeline.
SYSTEM_SENTENCE_INDEX = 0xFFFF
_HEADER = struct.Struct("<BHHB")
HEADER_BYTES = _HEADER.size


def pack_audio(turn: int, sentence: int, pcm: bytes, *, flags: int = 0) -> bytes:
    return _HEADER.pack(AUDIO_KIND_PCM16, turn & 0xFFFF, sentence & 0xFFFF, flags) + pcm


def unpack_audio(frame: bytes) -> tuple[int, int, int, bytes]:
    """→ (turn, sentence, flags, pcm). Raises ValueError on a foreign frame."""
    if len(frame) < HEADER_BYTES:
        raise ValueError("short audio frame")
    kind, turn, sentence, flags = _HEADER.unpack_from(frame)
    if kind != AUDIO_KIND_PCM16:
        raise ValueError(f"unknown audio frame kind {kind}")
    return turn, sentence, flags, frame[HEADER_BYTES:]


# -- client → server -------------------------------------------------------------------


class ClientCapabilities(BaseModel):
    sample_rate: int = SAMPLE_RATE
    echo_cancellation: bool = True
    user_agent: str | None = None


class StartMessage(BaseModel):
    type: Literal["start"]
    token: str
    query_session_id: str | None = None
    client: ClientCapabilities = Field(default_factory=ClientCapabilities)


class ResumeMessage(BaseModel):
    type: Literal["resume"]
    token: str
    session_id: str
    resume_token: str


class TextMessage(BaseModel):
    """A typed turn inside voice mode (handoff without leaving the screen)."""

    type: Literal["text"]
    text: str = Field(min_length=1, max_length=2000)


class ConfirmMessage(BaseModel):
    """The tapped answer to a LASA confirmation."""

    type: Literal["confirm"]
    choice: str = Field(min_length=1, max_length=200)


class ContinueMessage(BaseModel):
    """Tap-to-continue after an interruption or a disclosure offer."""

    type: Literal["continue"]


class BargeInMessage(BaseModel):
    """Client detected user speech during playback and already stopped it."""

    type: Literal["barge_in"]
    stop_latency_ms: float = Field(ge=0)


class PlaybackMessage(BaseModel):
    """Client-side playback milestones (drive transcript sync + waterfall)."""

    type: Literal["playback"]
    turn: int
    sentence: int
    event: Literal["started", "ended"]
    # Client-measured ms from the first audio frame *received* for this
    # sentence to the first sample out of the speaker (jitter buffer +
    # playback start) — the last waterfall leg, measured where it happens.
    buffer_ms: float | None = None


class StopMessage(BaseModel):
    type: Literal["stop"]


class PingMessage(BaseModel):
    type: Literal["ping"]


ClientMessage = Annotated[
    StartMessage
    | ResumeMessage
    | TextMessage
    | ConfirmMessage
    | ContinueMessage
    | BargeInMessage
    | PlaybackMessage
    | StopMessage
    | PingMessage,
    Field(discriminator="type"),
]

client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def parse_client_message(payload: str | bytes) -> ClientMessage:
    return client_message_adapter.validate_json(payload)


# -- server → client -------------------------------------------------------------------


class SessionEvent(BaseModel):
    type: Literal["session"] = "session"
    session_id: str
    resume_token: str
    query_session_id: str | None
    backend: str
    stt_model: str
    tts_model: str
    sample_rate: int = SAMPLE_RATE
    state: str
    resumed: bool = False


class StateEvent(BaseModel):
    type: Literal["state"] = "state"
    state: str
    from_state: str
    reason: str
    turn: int


class WordOut(BaseModel):
    text: str
    confidence: float


class PartialEvent(BaseModel):
    type: Literal["partial"] = "partial"
    turn: int
    text: str
    words: list[WordOut] = Field(default_factory=list)


class CorrectionOut(BaseModel):
    original: str
    corrected: str
    score: float


class FinalEvent(BaseModel):
    type: Literal["final"] = "final"
    turn: int
    text: str
    raw_text: str
    corrections: list[CorrectionOut] = Field(default_factory=list)
    source: Literal["voice", "text"] = "voice"


class EndpointEvent(BaseModel):
    type: Literal["endpoint"] = "endpoint"
    turn: int
    layer: Literal["vad", "semantic", "ceiling", "text"]
    silence_ms: float
    complete: bool
    decision_ms: float


class SpeculationEvent(BaseModel):
    type: Literal["speculation"] = "speculation"
    turn: int
    fired: bool
    hit: bool | None
    similarity: float | None
    wasted: bool


class GuardrailEvent(BaseModel):
    type: Literal["guardrail"] = "guardrail"
    turn: int
    verdict: Literal["blocked", "escalation"]
    blocked_by: str | None
    code: str | None
    message: str
    spoken: str
    # The shared text/voice thread this turn was written to (provisioned on
    # the first turn of a session started without one).
    query_session_id: str | None = None


class ConfirmOption(BaseModel):
    name: str
    description: str


class ConfirmRequestEvent(BaseModel):
    type: Literal["confirm_request"] = "confirm_request"
    turn: int
    heard: str
    options: list[ConfirmOption]
    prompt: str


SentenceKind = Literal[
    "answer",
    "offer",
    "refusal",
    "escalation",
    "abstention",
    "confirmation",
    "mask",
    "system",
]


class AgentSentenceEvent(BaseModel):
    type: Literal["agent_sentence"] = "agent_sentence"
    turn: int
    index: int
    text: str  # what the transcript shows (with [n] markers)
    spoken_text: str  # what was sent to TTS
    markers: list[int] = Field(default_factory=list)
    kind: SentenceKind = "answer"


class AudioStartEvent(BaseModel):
    type: Literal["audio_start"] = "audio_start"
    turn: int
    index: int
    sample_rate: int = SAMPLE_RATE


class AudioEndEvent(BaseModel):
    type: Literal["audio_end"] = "audio_end"
    turn: int
    index: int
    # True when this was the last sentence of the turn's speech.
    last: bool = False


class ResultEvent(BaseModel):
    type: Literal["result"] = "result"
    turn: int
    query_id: str | None
    query_session_id: str | None = None
    data: dict[str, Any]


class OfferEvent(BaseModel):
    """Progressive disclosure: the agent has more and asked whether to go on."""

    type: Literal["offer"] = "offer"
    turn: int
    kind: Literal["walkthrough", "more"]
    remaining_sentences: int


class WaterfallEvent(BaseModel):
    type: Literal["waterfall"] = "waterfall"
    turn: int
    legs: dict[str, float | None]
    total_first_audio_ms: float | None
    client_first_audio_ms: float | None
    speculation: dict[str, Any]
    waste: dict[str, Any]
    mask_used: bool


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str


class PongEvent(BaseModel):
    type: Literal["pong"] = "pong"


ServerEvent = (
    SessionEvent
    | StateEvent
    | PartialEvent
    | FinalEvent
    | EndpointEvent
    | SpeculationEvent
    | GuardrailEvent
    | ConfirmRequestEvent
    | AgentSentenceEvent
    | AudioStartEvent
    | AudioEndEvent
    | ResultEvent
    | OfferEvent
    | WaterfallEvent
    | ErrorEvent
    | PongEvent
)
