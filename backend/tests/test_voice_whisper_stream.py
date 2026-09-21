"""The offline recognizer's decode scheduling (11B/11C on the offline backend).

faster-whisper is not needed: a fake model with a controllable decode delay
stands in, and the tests check *which* audio each decode covered — the
property the endpoint decision depends on.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from app.voice.stt.base import SttEvent
from app.voice.stt.whisper_local import _MAX_HOTWORD_TOKENS, WhisperStream, hotword_prompt

_FRAME_MS = 20
_LOUD = (np.full(16 * _FRAME_MS, 8000, dtype=np.int16)).tobytes()


def _loud_audio(ms: int) -> bytes:
    return _LOUD * (ms // _FRAME_MS)


class _FakeModel:
    """Sleeps ``delay_s`` per decode and returns one word per 100 ms of audio."""

    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s
        self.decodes = 0

    def transcribe(self, audio: Any, **_: Any) -> tuple[list[Any], None]:
        self.decodes += 1
        time.sleep(self.delay_s)
        n = max(1, int(audio.size / 1600))
        words = [
            SimpleNamespace(word=f"w{i}", probability=0.9, start=i * 0.1, end=i * 0.1 + 0.1)
            for i in range(n)
        ]
        return [SimpleNamespace(words=words, avg_logprob=-0.1, no_speech_prob=0.01)], None


async def _collect(stream: WhisperStream, out: list[SttEvent]) -> None:
    async for event in stream.events():
        out.append(event)


async def _wait_for_hint(stream: WhisperStream) -> None:
    hint = stream._hint
    if hint is not None:
        await hint


@pytest.mark.asyncio
async def test_a_speech_end_hint_behind_an_earlier_one_is_run_not_dropped() -> None:
    model = _FakeModel(delay_s=0.15)
    stream = WhisperStream(model, hotwords=None, partial_step_ms=1000, min_partial_ms=900)
    events: list[SttEvent] = []
    collector = asyncio.create_task(_collect(stream, events))

    await stream.send_audio(_loud_audio(1000))  # a partial starts on this snapshot
    await asyncio.sleep(0)
    await stream.send_audio(_loud_audio(100))  # more speech the partial does not cover
    await stream.hint_speech_end()  # a pause between phrases: hint #1
    await asyncio.sleep(0.02)
    await stream.send_audio(_loud_audio(200))  # "…for CAP?" — then silence
    await stream.hint_speech_end()  # hint #1 is still decoding: must coalesce

    await _wait_for_hint(stream)
    await stream.close()
    await collector

    partials = [e for e in events if e.kind == "partial"]
    assert stream.hint_decodes == 2, "the second hint ran once the first landed"
    assert partials[-1].covered_ms == 1300.0, "the last decode covers the last word"
    assert [p.covered_ms for p in partials if p.covered_ms is not None][-2:] == [1100.0, 1300.0]


@pytest.mark.asyncio
async def test_no_hint_when_the_partial_in_flight_already_covers_the_last_word() -> None:
    model = _FakeModel(delay_s=0.1)
    stream = WhisperStream(model, hotwords=None, partial_step_ms=1000, min_partial_ms=900)
    events: list[SttEvent] = []
    collector = asyncio.create_task(_collect(stream, events))

    await stream.send_audio(_loud_audio(1000))  # partial snapshot = every loud frame
    await asyncio.sleep(0)
    await stream.hint_speech_end()  # nothing new to cover
    assert stream.hint_decodes == 0
    inflight = stream._inflight
    assert inflight is not None
    await inflight
    await stream.close()
    await collector

    assert model.decodes == 1
    assert [e.covered_ms for e in events if e.kind == "partial"] == [1000.0]


@pytest.mark.asyncio
async def test_the_hint_covers_the_sessions_speech_end_not_the_streams_loudness_guess() -> None:
    """The session's VAD heard speech 10 ms past the partial's snapshot: the
    partial is not the speech-end decode, however loud this stream thought
    those frames were, so a hint must run."""
    model = _FakeModel(delay_s=0.1)
    stream = WhisperStream(model, hotwords=None, partial_step_ms=1000, min_partial_ms=900)
    events: list[SttEvent] = []
    collector = asyncio.create_task(_collect(stream, events))

    await stream.send_audio(_loud_audio(1000))  # partial snapshot: 1000 ms
    await asyncio.sleep(0)
    await stream.send_audio(_loud_audio(20))
    await stream.hint_speech_end(speech_end_ms=1010.0)
    assert stream._hint is not None, "not skipped as covered by the partial"
    await _wait_for_hint(stream)
    await stream.close()
    await collector

    assert stream.hint_decodes == 1
    covered = [e.covered_ms for e in events if e.kind == "partial"]
    assert covered[-1] == 1020.0


@pytest.mark.asyncio
async def test_a_hint_that_already_covers_the_end_does_not_rerun() -> None:
    model = _FakeModel(delay_s=0.1)
    stream = WhisperStream(model, hotwords=None, partial_step_ms=5000, min_partial_ms=900)
    events: list[SttEvent] = []
    collector = asyncio.create_task(_collect(stream, events))

    await stream.send_audio(_loud_audio(1000))
    await stream.hint_speech_end()
    await stream.hint_speech_end()  # a second call with no new speech
    await _wait_for_hint(stream)
    await stream.finalize()  # reuses the hint decode: no further decode
    await stream.close()
    await collector

    assert stream.hint_decodes == 1 and model.decodes == 1
    assert [e.kind for e in events] == ["partial", "final"]


class _FakeTokenizer:
    """Five tokens per term (drug names split into many BPE pieces)."""

    def encode(self, text: str) -> SimpleNamespace:
        return SimpleNamespace(ids=[0] * (5 * len([t for t in text.split(",") if t.strip()])))


def test_hotword_prompt_takes_the_top_terms_up_to_the_token_budget() -> None:
    boost = [f"drug{i}" for i in range(40)]
    prompt = hotword_prompt(SimpleNamespace(hf_tokenizer=_FakeTokenizer()), boost)
    assert prompt is not None
    chosen = prompt.split(", ")
    assert chosen == boost[: len(chosen)], "the head of the ranked list, in order"
    assert 5 * len(chosen) <= _MAX_HOTWORD_TOKENS < 5 * (len(chosen) + 1)
    assert hotword_prompt(SimpleNamespace(hf_tokenizer=_FakeTokenizer()), []) is None
    assert hotword_prompt(SimpleNamespace(), boost[:3]) == "drug0, drug1, drug2"
