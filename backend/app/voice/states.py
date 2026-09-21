"""The per-connection session state machine (11A.4).

    IDLE → LISTENING → ENDPOINTING → PROCESSING → SPEAKING
                                        ↑            │
                                        │            ├─ BARGE_IN → LISTENING
                                        │            └─ (done)  → LISTENING
    PROCESSING → CONFIRMING → LISTENING (LASA gate: the agent asks, then listens)

Every transition is pushed to the client (it drives the UI) and written as a
structured log line (it drives the waterfall). Illegal transitions raise —
a state bug must surface in tests, never as a silently stuck session.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

import structlog

logger = structlog.stdlib.get_logger("app.voice.state")


class VoiceState(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    ENDPOINTING = "ENDPOINTING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    BARGE_IN = "BARGE_IN"
    CONFIRMING = "CONFIRMING"
    CLOSED = "CLOSED"


_ALLOWED: dict[VoiceState, frozenset[VoiceState]] = {
    VoiceState.IDLE: frozenset({VoiceState.LISTENING, VoiceState.CLOSED}),
    VoiceState.LISTENING: frozenset(
        {VoiceState.ENDPOINTING, VoiceState.PROCESSING, VoiceState.IDLE, VoiceState.CLOSED}
    ),
    # ENDPOINTING can fall back to LISTENING: the user resumed mid-thought.
    VoiceState.ENDPOINTING: frozenset(
        {VoiceState.PROCESSING, VoiceState.LISTENING, VoiceState.IDLE, VoiceState.CLOSED}
    ),
    VoiceState.PROCESSING: frozenset(
        {
            VoiceState.SPEAKING,
            VoiceState.CONFIRMING,
            VoiceState.LISTENING,  # blocked/cancelled before any audio
            VoiceState.ENDPOINTING,  # the final transcript was incomplete: keep waiting
            VoiceState.IDLE,
            VoiceState.CLOSED,
        }
    ),
    VoiceState.SPEAKING: frozenset(
        {
            VoiceState.BARGE_IN,
            VoiceState.LISTENING,
            VoiceState.CONFIRMING,  # the confirmation question has been asked
            VoiceState.IDLE,
            VoiceState.CLOSED,
        }
    ),
    VoiceState.BARGE_IN: frozenset({VoiceState.LISTENING, VoiceState.IDLE, VoiceState.CLOSED}),
    VoiceState.CONFIRMING: frozenset(
        {
            VoiceState.LISTENING,
            VoiceState.PROCESSING,
            VoiceState.SPEAKING,  # "I didn't catch that — which one?"
            VoiceState.IDLE,
            VoiceState.CLOSED,
        }
    ),
    VoiceState.CLOSED: frozenset(),
}


class IllegalTransition(RuntimeError):
    pass


@dataclass(slots=True)
class Transition:
    from_state: VoiceState
    to_state: VoiceState
    reason: str
    at_ms: float  # monotonic milliseconds


TransitionListener = Callable[[Transition], None]


@dataclass(slots=True)
class SessionStateMachine:
    session_id: str
    state: VoiceState = VoiceState.IDLE
    history: list[Transition] = field(default_factory=list)
    _listeners: list[TransitionListener] = field(default_factory=list)

    def on_transition(self, listener: TransitionListener) -> None:
        self._listeners.append(listener)

    def can(self, to_state: VoiceState) -> bool:
        return to_state in _ALLOWED[self.state]

    def transition(self, to_state: VoiceState, reason: str = "") -> Transition:
        if to_state == self.state:
            # Re-entering the same state is a no-op, not an error (e.g. two
            # rapid barge-in signals). Still returns a record for callers.
            return Transition(self.state, to_state, f"noop:{reason}", time.monotonic() * 1000)
        if not self.can(to_state):
            raise IllegalTransition(f"{self.state} → {to_state} ({reason or 'no reason'})")
        record = Transition(self.state, to_state, reason, time.monotonic() * 1000)
        self.state = to_state
        self.history.append(record)
        logger.info(
            "voice_state",
            session_id=self.session_id,
            from_state=record.from_state.value,
            to_state=record.to_state.value,
            reason=reason,
        )
        for listener in list(self._listeners):
            listener(record)
        return record
