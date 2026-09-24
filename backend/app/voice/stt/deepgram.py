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

import httpx
import structlog
import websockets
from websockets.exceptions import InvalidStatus

from app.voice.availability import CLOUD_SETUP, key_configured
from app.voice.stt.base import QueueEvents, SttEvent, SttWord

logger = structlog.stdlib.get_logger("app.voice.stt.deepgram")

_ENDPOINT = "wss://api.deepgram.com/v1/listen"
_REST_ENDPOINT = "https://api.deepgram.com/v1/listen"


def handshake_refusal(exc: InvalidStatus) -> tuple[int, str]:
    """Deepgram's reason for refusing a WebSocket: it rides in the dg-error
    header (the body is often empty). Logged, so a refusal says why."""
    response = exc.response
    reason = response.headers.get("dg-error") or ""
    if not reason and response.body:
        reason = response.body.decode("utf-8", "replace")[:300]
    return response.status_code, reason


_KEEPALIVE_SECONDS = 5.0
# Deepgram rejects a request whose keyterms exceed 500 tokens in total
# ("Keyterm limit exceeded") and recommends the 20-50 most important terms.
# The boost list arrives ranked (corpus drugs by document count, then LASA
# names), so the budget keeps the head of it. Token counts are estimated
# conservatively — drug names tokenize poorly — to stay well inside the cap.
_MAX_KEYTERMS = 50
_KEYTERM_TOKEN_BUDGET = 400


def _estimated_tokens(term: str) -> int:
    return max(1, -(-len(term) // 3))


def select_keyterms(boost: list[str]) -> list[str]:
    """The ranked terms that fit Deepgram's keyterm limit, in order."""
    chosen: list[str] = []
    spent = 0
    for term in boost:
        cost = _estimated_tokens(term)
        if len(chosen) >= _MAX_KEYTERMS or spent + cost > _KEYTERM_TOKEN_BUDGET:
            break
        chosen.append(term)
        spent += cost
    return chosen


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

    def unavailable_reason(self) -> str | None:
        return None if key_configured(self._api_key) else CLOUD_SETUP

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
        keyterms = select_keyterms(boost)
        try:
            socket = await self._connect([*params, *(("keyterm", t) for t in keyterms)])
        except InvalidStatus as exc:
            status, reason = handshake_refusal(exc)
            logger.warning("deepgram_refused", status=status, reason=reason, keyterms=len(keyterms))
            # A vocabulary Deepgram will not take must not cost the session:
            # hearing without the boost beats not hearing at all.
            if status != 400 or not keyterms:
                raise
            socket = await self._connect(params)
            keyterms = []
        logger.info("deepgram_connected", model=self.model, keyterms=len(keyterms))
        return DeepgramStream(socket, model=self.model)

    async def _connect(self, params: list[tuple[str, str]]) -> Any:
        return await websockets.connect(
            f"{_ENDPOINT}?{urlencode(params)}",
            additional_headers={"Authorization": f"Token {self._api_key}"},
            max_size=None,
        )

    async def transcribe(
        self, audio: bytes, content_type: str, *, client: httpx.AsyncClient | None = None
    ) -> str:
        """One finished recording → its text (Deepgram's pre-recorded API).

        For typing by voice in the chat box: the browser records a clip and
        sends it whole, so there is no stream to hold open."""
        owned = client is None
        http = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
        try:
            response = await http.post(
                _REST_ENDPOINT,
                params={
                    "model": self.model,
                    "smart_format": "true",
                    "punctuate": "true",
                    # nova-3-medical is English-only; say so rather than
                    # ask it to detect.
                    "language": "en",
                },
                headers={"Authorization": f"Token {self._api_key}", "Content-Type": content_type},
                content=audio,
            )
        finally:
            if owned:
                await http.aclose()
        if response.status_code != 200:
            logger.warning(
                "deepgram_transcribe_failed",
                status=response.status_code,
                detail=response.text[:300],
            )
            raise RuntimeError(f"Deepgram answered {response.status_code}")
        channels = (response.json().get("results") or {}).get("channels") or []
        alternatives = (channels[0].get("alternatives") if channels else None) or []
        return str(alternatives[0].get("transcript", "")).strip() if alternatives else ""
