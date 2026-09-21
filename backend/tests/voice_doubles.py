"""Scripted STT/TTS doubles for the voice tests.

They stand in for the *edge providers only* — the state machine, endpointing,
correction, LASA gate, guardrails, graph, rendering, waterfall and
persistence all run for real. The STT double "hears" a scripted utterance as
speech-level audio arrives (one word per 200 ms, like a streaming
recognizer's interim results) and finalizes it on demand; the TTS double
yields silent PCM in 60 ms chunks with a small delay so barge-in can land
mid-sentence.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from app.voice.audio import FRAME_BYTES, rms
from app.voice.stt.base import QueueEvents, SttEvent, SttWord


@dataclass(slots=True)
class Utterance:
    text: str
    # Per-word confidence overrides (word → confidence); default 0.95.
    confidences: dict[str, float] = field(default_factory=dict)
    # Emit the words as a partial without waiting for the finalize call.
    words_per_step: int = 1
    # What finalize() reports, when it differs from the partials (a streaming
    # recognizer's final pass often adds the trailing word: "…treatment for").
    final_text: str | None = None


def tone_frame(*, amplitude: float = 0.3, hz: float = 220.0, phase: int = 0) -> bytes:
    """One 20 ms frame of a sine tone — 'speech' as far as the energy VAD knows."""
    samples = bytearray()
    for i in range(FRAME_BYTES // 2):
        value = int(amplitude * 32767 * math.sin(2 * math.pi * hz * (phase + i) / 16000))
        samples += int(value).to_bytes(2, "little", signed=True)
    return bytes(samples)


SILENCE_FRAME = b"\x00" * FRAME_BYTES


class ScriptedSttStream:
    def __init__(self, script: list[Utterance]) -> None:
        self._script = list(script)
        self._events = QueueEvents()
        self._current: Utterance | None = None
        self._heard = 0
        self._speech_ms = 0.0
        self.finalized: list[str] = []
        self.audio_bytes = 0

    def _take(self) -> Utterance | None:
        if self._current is None and self._script:
            self._current = self._script.pop(0)
            self._heard = 0
            self._speech_ms = 0.0
        return self._current

    async def send_audio(self, pcm: bytes) -> None:
        self.audio_bytes += len(pcm)
        if rms(pcm) < 0.01:
            return
        utterance = self._take()
        if utterance is None:
            return
        self._speech_ms += 20.0
        words = utterance.text.split()
        target = min(len(words), int(self._speech_ms // 200) * utterance.words_per_step)
        if target > self._heard:
            self._heard = target
            self._events.emit(
                SttEvent(
                    "partial", text=" ".join(words[:target]), words=self._words(words[:target])
                )
            )

    def _words(self, words: list[str]) -> list[SttWord]:
        assert self._current is not None
        return [
            SttWord(w, self._current.confidences.get(w.strip(".,?!").lower(), 0.95)) for w in words
        ]

    async def finalize(self) -> None:
        utterance = self._current
        self._current = None
        if utterance is None:
            self._events.emit(SttEvent("final", text="", words=[]))
            return
        text = utterance.final_text if utterance.final_text is not None else utterance.text
        words = text.split()
        self.finalized.append(text)
        self._events.emit(SttEvent("final", text=text, words=self._words_for(utterance, words)))

    @staticmethod
    def _words_for(utterance: Utterance, words: list[str]) -> list[SttWord]:
        return [SttWord(w, utterance.confidences.get(w.strip(".,?!").lower(), 0.95)) for w in words]

    async def hint_speech_end(self, *, speech_end_ms: float | None = None) -> None:
        """A streaming recognizer re-emits the utterance after a pause."""
        utterance = self._current
        if utterance is None or self._heard == 0:
            return
        words = utterance.text.split()[: self._heard]
        self._events.emit(SttEvent("partial", text=" ".join(words), words=self._words(words)))

    async def reset(self) -> None:
        if self._current is not None and self._heard == 0:
            self._script.insert(0, self._current)  # nothing heard yet: keep it
        self._current = None

    def events(self) -> AsyncIterator[SttEvent]:
        return self._events.__aiter__()

    async def close(self) -> None:
        self._events.end()


class ScriptedSttProvider:
    name = "scripted"
    model = "scripted-v1"

    def __init__(self, script: list[Utterance]) -> None:
        self.script = script
        self.streams: list[ScriptedSttStream] = []
        self.boost_seen: list[str] = []

    async def open(self, *, boost: list[str]) -> ScriptedSttStream:
        self.boost_seen = list(boost)
        stream = ScriptedSttStream(self.script)
        self.streams.append(stream)
        return stream


class ScriptedTtsStream:
    model = "scripted-tts"

    def __init__(self, *, chunks_per_sentence: int = 4, chunk_delay_s: float = 0.03) -> None:
        self._chunks = chunks_per_sentence
        self._delay = chunk_delay_s
        self._cancelled = False
        self.spoken: list[str] = []
        self.cancels = 0

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        self._cancelled = False
        self.spoken.append(text)
        for _ in range(self._chunks):
            await asyncio.sleep(self._delay)
            if self._cancelled:  # set by cancel() while we slept
                return
            yield b"\x00" * (16_000 * 2 * 60 // 1000)

    async def cancel(self) -> None:
        self._cancelled = True
        self.cancels += 1

    async def close(self) -> None:
        self._cancelled = True


class ScriptedTtsProvider:
    name = "scripted"

    def __init__(self, **stream_kwargs: float) -> None:
        self._kwargs = stream_kwargs
        self.streams: list[ScriptedTtsStream] = []
        self.lexicon_pls: str | None = None

    async def open(self, *, quality: str, lexicon_pls: str | None) -> ScriptedTtsStream:
        self.lexicon_pls = lexicon_pls
        stream = ScriptedTtsStream(**self._kwargs)  # type: ignore[arg-type]
        self.streams.append(stream)
        return stream
