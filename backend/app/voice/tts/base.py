"""The TTS provider contract."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class TtsStream(Protocol):
    model: str

    def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """Stream 16 kHz PCM16 chunks for one sentence. Stops early on cancel."""
        ...

    async def cancel(self) -> None:
        """Abort the sentence in progress and drop any audio not yet yielded."""
        ...

    async def close(self) -> None: ...


class TtsProvider(Protocol):
    name: str

    async def open(self, *, quality: str, lexicon_pls: str | None) -> TtsStream:
        """Pre-warm a connection so the TLS/auth handshake is off the critical path."""
        ...
