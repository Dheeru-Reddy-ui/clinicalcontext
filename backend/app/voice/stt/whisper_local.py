"""Offline STT: faster-whisper on the CPU (the ``offline`` voice backend).

Whisper is not a streaming recognizer, so streaming is emulated honestly:
while audio accumulates, the utterance-so-far is re-decoded whenever at least
``partial_step_ms`` of new audio has arrived (one partial in flight at a
time, in a worker thread); a pause after speech asks for a decode right
away — queued behind any partial in flight, never dropped — so the endpoint
decision finds a transcript that covers the last word; ``finalize`` reuses
whichever decode already covered every speech
frame, or decodes once more. Each decode costs the same ~0.6 s on the CPU
regardless of utterance length (the encoder runs on a 30 s window), so what
matters is never *waiting* for one that was skipped. The resulting latency
is what the waterfall reports — no allowance is made.

Decoding is biased toward the corpus vocabulary via ``hotwords`` (the local
equivalent of Deepgram keyterms) — a prompt the decoder pays for on every
call, flat up to ~80 tokens and then roughly double (measured), so the
prompt takes the top-ranked terms up to that budget, not a fixed count.
Word probabilities feed the LASA gate.
Whisper hallucinates on trailing silence; utterances are silence-trimmed
before decoding and low-probability trailing segments are dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import structlog

from app.voice.audio import pcm16_to_float, rms, trim_silence
from app.voice.stt.base import QueueEvents, SttEvent, SttWord

logger = structlog.stdlib.get_logger("app.voice.stt.whisper")

_MODELS: dict[tuple[str, str, int], Any] = {}
_MODEL_LOCK = threading.Lock()
# Partials and speech-end hints share one lane: two decodes at once do not
# overlap on the CPU (each already spreads across the cores — measured: the
# slower of a concurrent pair lands at ~2.1x a solo decode), so a hint that
# queues behind an in-flight partial finishes sooner than one racing it.
# The final lane exists only for ``finalize``'s fallback decode.
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper-decode")
_FINAL_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper-final")

# Prompt tokens the decoder can carry without slowing down; beyond ~100 the
# decode cost doubles (tiny.en, int8: 77 tokens → 483 ms, 149 → 920 ms).
_MAX_HOTWORD_TOKENS = 80
_MAX_HOTWORDS = 40  # the cap when the tokenizer is unavailable


def _load_model(name: str, compute_type: str, cpu_threads: int) -> Any:
    key = (name, compute_type, cpu_threads)
    with _MODEL_LOCK:
        model = _MODELS.get(key)
        if model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:  # pragma: no cover — environment
                raise RuntimeError(
                    "faster-whisper is not installed: run `uv sync --group voice`"
                ) from exc
            started = time.perf_counter()
            model = WhisperModel(
                name, device="cpu", compute_type=compute_type, cpu_threads=cpu_threads
            )
            logger.info(
                "whisper_model_loaded",
                model=name,
                compute_type=compute_type,
                load_ms=round((time.perf_counter() - started) * 1000),
            )
            _MODELS[key] = model
        return model


@dataclass(slots=True)
class Decoded:
    text: str
    words: list[SttWord]
    covered_bytes: int
    decode_ms: float


def _decode(model: Any, pcm: bytes, hotwords: str | None) -> Decoded:
    started = time.perf_counter()
    audio = pcm16_to_float(trim_silence(pcm))
    if audio.size < 1600:  # < 100 ms of speech: nothing to decode
        return Decoded("", [], len(pcm), 0.0)
    seconds = audio.size / 16000
    segments, _info = model.transcribe(
        audio,
        beam_size=1,
        temperature=0.0,
        language="en",
        word_timestamps=True,
        hotwords=hotwords,
        condition_on_previous_text=False,
        no_repeat_ngram_size=3,
        max_new_tokens=int(seconds * 8) + 16,
    )
    words: list[SttWord] = []
    for index, segment in enumerate(segments):
        segment_words = list(segment.words or [])
        if not segment_words:
            continue
        mean_p = sum(float(w.probability) for w in segment_words) / len(segment_words)
        # Hallucinated tails: a later segment the model itself does not believe.
        if index > 0 and (
            segment.avg_logprob < -0.8
            or (mean_p < 0.5 and (segment.no_speech_prob > 0.15 or segment.avg_logprob < -0.25))
        ):
            continue
        for w in segment_words:
            words.append(
                SttWord(
                    text=str(w.word).strip(),
                    confidence=float(w.probability),
                    start_ms=float(w.start) * 1000,
                    end_ms=float(w.end) * 1000,
                )
            )
    while words and words[-1].confidence < 0.05:
        words.pop()
    text = " ".join(w.text for w in words).strip()
    decode_ms = round((time.perf_counter() - started) * 1000, 1)
    logger.debug("whisper_decode", audio_s=round(seconds, 2), decode_ms=decode_ms, words=len(words))
    return Decoded(text, words, len(pcm), decode_ms)


class WhisperStream:
    def __init__(
        self,
        model: Any,
        *,
        hotwords: str | None,
        partial_step_ms: int = 1000,
        min_partial_ms: int = 900,
    ) -> None:
        self._model = model
        self._hotwords = hotwords
        self._events = QueueEvents()
        self._buffer = bytearray()
        self._partial_step = partial_step_ms * 32  # bytes: 16 kHz * 2 bytes / 1000 ms
        self._min_partial = min_partial_ms * 32
        self._decoded_upto = 0
        self._last_loud_bytes = 0
        self._base_bytes = 0  # audio discarded by earlier finalize()/reset() calls
        self._inflight: asyncio.Task[Decoded] | None = None
        self._inflight_bytes = 0  # buffer length the in-flight partial snapshotted
        self._hint: asyncio.Task[Decoded] | None = None
        self._hint_target = 0  # buffer length a speech-end decode must cover
        self._generation = 0
        self._closed = False
        self.last_decode_ms: float | None = None
        self.hint_decodes = 0

    async def send_audio(self, pcm: bytes) -> None:
        if self._closed:
            return
        self._buffer.extend(pcm)
        if rms(pcm) > 0.01:
            self._last_loud_bytes = len(self._buffer)
        hint_busy = self._hint is not None and not self._hint.done()
        if (
            not hint_busy
            and self._inflight is None
            and len(self._buffer) >= self._min_partial
            and len(self._buffer) - self._decoded_upto >= self._partial_step
            and self._last_loud_bytes > self._decoded_upto  # new speech, not just silence
        ):
            self._inflight_bytes = len(self._buffer)
            self._inflight = asyncio.create_task(self._run_partial(self._generation))

    async def _run_partial(self, generation: int) -> Decoded:
        try:
            return await self._decode_now(generation, _EXECUTOR, lane="partial")
        finally:
            self._inflight = None

    async def _decode_now(
        self, generation: int, executor: ThreadPoolExecutor, *, lane: str
    ) -> Decoded:
        snapshot = bytes(self._buffer)
        base_bytes = self._base_bytes
        queued_at = time.perf_counter()
        loop = asyncio.get_running_loop()
        decoded = await loop.run_in_executor(
            executor, _decode, self._model, snapshot, self._hotwords
        )
        self.last_decode_ms = decoded.decode_ms
        logger.debug(
            "whisper_partial",
            lane=lane,
            covered_ms=(base_bytes + decoded.covered_bytes) / 32,
            landed_at_ms=self._stream_ms(),
            wait_ms=round((time.perf_counter() - queued_at) * 1000 - decoded.decode_ms),
            decode_ms=decoded.decode_ms,
            stale=generation != self._generation,
        )
        if generation == self._generation and not self._closed:
            self._decoded_upto = max(self._decoded_upto, decoded.covered_bytes)
            if decoded.text:
                self._events.emit(
                    SttEvent(
                        "partial",
                        text=decoded.text,
                        words=decoded.words,
                        covered_ms=(base_bytes + decoded.covered_bytes) / 32,
                    )
                )
        return decoded

    async def hint_speech_end(self, *, speech_end_ms: float | None = None) -> None:
        """The session saw silence after speech: decode what is buffered now,
        so the commit ~300 ms later finds a decode that already covers it.

        ``speech_end_ms`` is the session's last speech frame in stream time —
        the session's VAD decides what counts as speech, not this stream's
        loudness check, and a decode must cover *that* frame to be fresh.
        Hints coalesce: a pause between phrases fires one too, and if the
        user then says more and stops, the hint that covers their last word
        runs the moment the earlier one lands rather than being dropped. A
        partial already in flight that snapshotted the last speech frame is
        that decode; no hint is needed.
        """
        if self._closed or len(self._buffer) < self._min_partial:
            return
        target = len(self._buffer) if speech_end_ms is None else self._bytes_at(speech_end_ms)
        self._hint_target = max(self._hint_target, min(target, len(self._buffer)))
        partial_busy = self._inflight is not None and not self._inflight.done()
        if partial_busy and self._inflight_bytes >= self._hint_target:
            logger.debug("whisper_hint", outcome="covered_by_partial", at_ms=self._stream_ms())
            return
        if self._hint is not None and not self._hint.done():
            logger.debug("whisper_hint", outcome="coalesced", at_ms=self._stream_ms())
            return
        logger.debug(
            "whisper_hint",
            outcome="queued" if partial_busy else "started",
            at_ms=self._stream_ms(),
        )
        self._hint = asyncio.create_task(self._run_hint(self._generation))

    def _bytes_at(self, stream_ms: float) -> int:
        return max(0, int(stream_ms * 32) - self._base_bytes)

    def _stream_ms(self) -> float:
        return (self._base_bytes + len(self._buffer)) / 32

    async def _run_hint(self, generation: int) -> Decoded:
        while True:
            self.hint_decodes += 1
            decoded = await self._decode_now(generation, _EXECUTOR, lane="hint")
            if generation != self._generation or self._closed:
                return decoded
            if decoded.covered_bytes >= self._hint_target:
                return decoded  # a later hint asked for nothing this did not cover

    async def finalize(self) -> None:
        self._generation += 1  # a late partial from before this point is stale
        inflight = self._inflight
        hint = self._hint
        self._inflight = None
        self._hint = None
        self._hint_target = 0
        snapshot = bytes(self._buffer)
        last_loud = self._last_loud_bytes
        self._base_bytes += len(self._buffer)
        self._buffer = bytearray()
        self._decoded_upto = 0
        self._last_loud_bytes = 0
        decoded: Decoded | None = None
        # A decode that already covers every speech frame is the final
        # transcript; trailing silence adds nothing but hallucinations. The
        # speech-end decode is checked first (it is the one meant for this).
        for task in (hint, inflight):
            if task is None or decoded is not None:
                continue
            with contextlib.suppress(Exception):
                candidate = await task
                if candidate.covered_bytes >= last_loud:
                    decoded = candidate
        if decoded is None:
            loop = asyncio.get_running_loop()
            decoded = await loop.run_in_executor(
                _FINAL_EXECUTOR, _decode, self._model, snapshot, self._hotwords
            )
            self.last_decode_ms = decoded.decode_ms
        self._events.emit(SttEvent("final", text=decoded.text, words=decoded.words))

    async def reset(self) -> None:
        self._generation += 1
        self._base_bytes += len(self._buffer)
        self._buffer = bytearray()
        self._decoded_upto = 0
        self._last_loud_bytes = 0
        self._hint_target = 0

    def events(self) -> AsyncIterator[SttEvent]:
        return self._events.__aiter__()

    async def close(self) -> None:
        self._closed = True
        self._generation += 1
        for task in (self._inflight, self._hint):
            if task is not None:
                with contextlib.suppress(Exception):
                    await task
        self._events.end()


class WhisperProvider:
    name = "whisper"

    def __init__(
        self, *, model: str = "tiny.en", compute_type: str = "int8", cpu_threads: int = 0
    ) -> None:
        self.model = model
        self._compute_type = compute_type
        self._cpu_threads = cpu_threads

    def unavailable_reason(self) -> str | None:
        import importlib.util

        if importlib.util.find_spec("faster_whisper") is not None:
            return None
        from app.voice.availability import CLOUD_SETUP

        return (
            "This server has no speech-recognition engine installed — the free "
            "deployment leaves it out to fit in memory. " + CLOUD_SETUP
        )

    async def warm(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            _EXECUTOR, _load_model, self.model, self._compute_type, self._cpu_threads
        )

    async def open(self, *, boost: list[str]) -> WhisperStream:
        loop = asyncio.get_running_loop()
        model = await loop.run_in_executor(
            _EXECUTOR, _load_model, self.model, self._compute_type, self._cpu_threads
        )
        return WhisperStream(model, hotwords=hotword_prompt(model, boost))


def hotword_prompt(model: Any, boost: list[str]) -> str | None:
    """The top-ranked boost terms that fit the decoder's prompt budget."""
    tokenizer = getattr(model, "hf_tokenizer", None)
    if tokenizer is None:
        return ", ".join(boost[:_MAX_HOTWORDS]) or None
    chosen: list[str] = []
    for term in boost[:_MAX_HOTWORDS]:
        candidate = ", ".join([*chosen, term])
        if len(tokenizer.encode(" " + candidate).ids) > _MAX_HOTWORD_TOKENS:
            break
        chosen.append(term)
    return ", ".join(chosen) or None


def decode_pcm_sync(
    pcm: bytes, *, model: str, hotwords: list[str], compute_type: str = "int8"
) -> Decoded:
    """One-shot decode (the eval harness's WER baseline without the pipeline)."""
    loaded = _load_model(model, compute_type, 0)
    return _decode(loaded, pcm, hotword_prompt(loaded, hotwords))


__all__ = ["Decoded", "WhisperProvider", "WhisperStream", "decode_pcm_sync", "hotword_prompt"]
