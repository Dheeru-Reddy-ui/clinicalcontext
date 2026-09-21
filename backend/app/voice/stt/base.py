"""The STT provider contract."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol

SttEventKind = Literal["partial", "final", "speech_started", "utterance_end", "error"]


@dataclass(slots=True)
class SttWord:
    text: str
    confidence: float
    start_ms: float | None = None
    end_ms: float | None = None


@dataclass(slots=True)
class SttEvent:
    kind: SttEventKind
    # For partial/final: the *whole utterance so far* (providers accumulate
    # their own finalized segments), never just the newest fragment.
    text: str = ""
    words: list[SttWord] = field(default_factory=list)
    message: str = ""
    # For partials: the stream time (ms of audio since the stream opened) this
    # text covers. A partial that was *emitted* after the user stopped may
    # have been decoded from audio that ends before their last word — the
    # session only treats a partial as fresh when its coverage reaches the
    # speech end. None → the provider cannot say; emission time is used.
    covered_ms: float | None = None


class SttStream(Protocol):
    async def send_audio(self, pcm: bytes) -> None: ...

    async def finalize(self) -> None:
        """Ask for a ``final`` event covering everything since the last final."""
        ...

    async def reset(self) -> None:
        """Discard audio/segments heard since the last final (no event)."""
        ...

    def events(self) -> AsyncIterator[SttEvent]: ...

    async def close(self) -> None: ...


class SttProvider(Protocol):
    name: str
    model: str

    async def open(self, *, boost: list[str]) -> SttStream: ...


class QueueEvents:
    """Shared event-queue plumbing for stream implementations."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[SttEvent | None] = asyncio.Queue()

    def emit(self, event: SttEvent) -> None:
        self._queue.put_nowait(event)

    def end(self) -> None:
        self._queue.put_nowait(None)

    async def __aiter__(self) -> AsyncIterator[SttEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event
