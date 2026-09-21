"""Deepgram streaming STT with ``nova-3-medical`` (11B.1).

Raw WebSocket client (no SDK): interim results on, smart formatting on,
Deepgram's own endpointing/VAD events forwarded, and the corpus-derived
vocabulary passed as Nova-3 ``keyterm`` prompts. Finalized segments are
accumulated so every partial/final event carries the whole utterance.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlencode

import structlog
import websockets

from app.voice.stt.base import QueueEvents, SttEvent, SttWord

logger = structlog.stdlib.get_logger("app.voice.stt.deepgram")

_ENDPOINT = "wss://api.deepgram.com/v1/listen"
_KEEPALIVE_SECONDS = 5.0
_MAX_KEYTERMS = 100


class DeepgramStream:
    def __init__(self, socket: Any, *, model: str) -> None:
        self._socket = socket
        self._model = model
        self._events = QueueEvents()
        self._finals: list[str] = []
        self._final_words: list[SttWord] = []
        self._finalize_waiters: list[asyncio.Future[None]] = []
        self._reader = asyncio.create_task(self._read(), name="deepgram-reader")
        self._keepalive = asyncio.create_task(self._keepalive_loop(), name="deepgram-keepalive")
        self._last_audio = asyncio.get_running_loop().time()

    async def send_audio(self, pcm: bytes) -> None:
        self._last_audio = asyncio.get_running_loop().time()
        await self._socket.send(pcm)

    async def finalize(self) -> None:
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._finalize_waiters.append(waiter)
        await self._socket.send(json.dumps({"type": "Finalize"}))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(waiter, timeout=1.5)
        self._emit_final()

    async def reset(self) -> None:
        self._finals.clear()
        self._final_words.clear()

    def events(self) -> AsyncIterator[SttEvent]:
        return self._events.__aiter__()

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._socket.send(json.dumps({"type": "CloseStream"}))
        self._keepalive.cancel()
        self._reader.cancel()
        with contextlib.suppress(Exception):
            await self._socket.close()
        self._events.end()

    # -- internals -------------------------------------------------------------------

    def _emit_final(self) -> None:
        text = " ".join(t for t in self._finals if t).strip()
        words = list(self._final_words)
        self._finals.clear()
        self._final_words.clear()
        self._events.emit(SttEvent("final", text=text, words=words))

    async def _keepalive_loop(self) -> None:
        while True:
            await asyncio.sleep(_KEEPALIVE_SECONDS)
            idle = asyncio.get_running_loop().time() - self._last_audio
            if idle >= _KEEPALIVE_SECONDS - 0.5:
                with contextlib.suppress(Exception):
                    await self._socket.send(json.dumps({"type": "KeepAlive"}))

    async def _read(self) -> None:
        try:
            async for raw in self._socket:
                if isinstance(raw, bytes):
                    continue
                message = json.loads(raw)
                kind = message.get("type")
                if kind == "Results":
                    self._on_results(message)
                elif kind == "SpeechStarted":
                    self._events.emit(SttEvent("speech_started"))
                elif kind == "UtteranceEnd":
                    self._events.emit(SttEvent("utterance_end"))
                elif kind == "Error":
                    self._events.emit(SttEvent("error", message=str(message)))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("deepgram_reader_ended", error=f"{type(exc).__name__}: {exc}")
            self._events.emit(SttEvent("error", message=f"{type(exc).__name__}: {exc}"))
        finally:
            self._events.end()

    def _on_results(self, message: dict[str, Any]) -> None:
        alternatives = (message.get("channel") or {}).get("alternatives") or []
        if not alternatives:
            return
        best = alternatives[0]
        transcript = str(best.get("transcript") or "").strip()
        covered_ms: float | None = None
        if "start" in message and "duration" in message:
            covered_ms = (float(message["start"]) + float(message["duration"])) * 1000
        words = [
            SttWord(
                text=str(w.get("punctuated_word") or w.get("word") or ""),
                confidence=float(w.get("confidence") or 0.0),
                start_ms=float(w.get("start", 0.0)) * 1000,
                end_ms=float(w.get("end", 0.0)) * 1000,
            )
            for w in best.get("words") or []
        ]
        if message.get("is_final"):
            if transcript:
                self._finals.append(transcript)
                self._final_words.extend(words)
            if message.get("from_finalize"):
                for waiter in self._finalize_waiters:
                    if not waiter.done():
                        waiter.set_result(None)
                self._finalize_waiters.clear()
            whole = " ".join(self._finals).strip()
            self._events.emit(
                SttEvent(
                    "partial", text=whole, words=list(self._final_words), covered_ms=covered_ms
                )
            )
            return
        whole = " ".join([*self._finals, transcript]).strip()
        self._events.emit(
            SttEvent(
                "partial",
                text=whole,
                words=[*self._final_words, *words],
                covered_ms=covered_ms,
            )
        )


class DeepgramProvider:
    name = "deepgram"

    def __init__(self, *, api_key: str, model: str = "nova-3-medical") -> None:
        self._api_key = api_key
        self.model = model

    async def open(self, *, boost: list[str]) -> DeepgramStream:
        params: list[tuple[str, str]] = [
            ("model", self.model),
            ("encoding", "linear16"),
            ("sample_rate", "16000"),
            ("channels", "1"),
            ("interim_results", "true"),
            ("smart_format", "true"),
            ("punctuate", "true"),
            ("vad_events", "true"),
            ("endpointing", "300"),
            ("utterance_end_ms", "1000"),
        ]
        params.extend(("keyterm", term) for term in boost[:_MAX_KEYTERMS])
        url = f"{_ENDPOINT}?{urlencode(params)}"
        socket = await websockets.connect(
            url,
            additional_headers={"Authorization": f"Token {self._api_key}"},
            max_size=None,
        )
        logger.info("deepgram_connected", model=self.model, keyterms=min(len(boost), _MAX_KEYTERMS))
        return DeepgramStream(socket, model=self.model)
