"""ElevenLabs streaming TTS over the ``stream-input`` WebSocket (11F.1).

The socket is opened at session start (pre-warmed) so the TLS + auth
handshake is not on the critical path of the first sentence. Sentences are
sent with ``flush`` so each one is synthesized immediately; audio arrives as
base64 PCM chunks and is yielded as it lands. Cancel closes the socket (the
API has no abort message) and pre-warms a replacement in the background.

Flash (``eleven_flash_v2_5``) is the latency model; Multilingual v2 is the
quality model — the org chooses (``organizations.voice_tts_quality``).
The corpus-derived pronunciation lexicon is uploaded once and referenced on
every synthesis.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog
import websockets

from app.voice.availability import key_configured

logger = structlog.stdlib.get_logger("app.voice.tts.elevenlabs")

_WS = "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"
_API = "https://api.elevenlabs.io/v1"


class ElevenLabsStream:
    def __init__(self, provider: ElevenLabsProvider, socket: Any, *, model: str) -> None:
        self._provider = provider
        self._socket = socket
        self.model = model
        self._cancelled = False
        self._lock = asyncio.Lock()

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        async with self._lock:
            self._cancelled = False
            socket = self._socket
            await socket.send(json.dumps({"text": text.strip() + " ", "flush": True}))
            while not self._cancelled:
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=20.0)
                except TimeoutError:
                    logger.warning("elevenlabs_timeout")
                    return
                except websockets.ConnectionClosed:
                    return
                message = json.loads(raw)
                audio = message.get("audio")
                if audio:
                    yield base64.b64decode(audio)
                if message.get("isFinal"):
                    return

    async def cancel(self) -> None:
        self._cancelled = True
        old = self._socket
        with contextlib.suppress(Exception):
            await old.close()
        # Replace the connection so the next sentence still starts warm.
        self._socket = await self._provider.connect(self.model)

    async def close(self) -> None:
        self._cancelled = True
        with contextlib.suppress(Exception):
            await self._socket.send(json.dumps({"text": ""}))
        with contextlib.suppress(Exception):
            await self._socket.close()


class ElevenLabsProvider:
    name = "elevenlabs"

    def __init__(
        self,
        *,
        api_key: str,
        voice_id: str,
        flash_model: str = "eleven_flash_v2_5",
        quality_model: str = "eleven_multilingual_v2",
    ) -> None:
        self._api_key = api_key
        self._voice_id = voice_id
        self._flash = flash_model
        self._quality = quality_model
        self._lexicon: tuple[str, str] | None = None  # (dictionary_id, version_id)

    async def ensure_lexicon(self, pls: str) -> None:
        """Upload the PLS lexicon once per process (idempotent by content)."""
        if self._lexicon is not None:
            return
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{_API}/pronunciation-dictionaries/add-from-file",
                headers={"xi-api-key": self._api_key},
                data={"name": "clinicalcontext-corpus"},
                files={"file": ("corpus.pls", pls.encode("utf-8"), "application/pls+xml")},
            )
            response.raise_for_status()
            payload = response.json()
        self._lexicon = (str(payload["id"]), str(payload["version_id"]))
        logger.info("elevenlabs_lexicon_uploaded", dictionary_id=self._lexicon[0])

    async def connect(self, model: str) -> Any:
        url = f"{_WS.format(voice_id=self._voice_id)}?model_id={model}&output_format=pcm_16000"
        socket = await websockets.connect(url, max_size=None)
        bos: dict[str, Any] = {
            "text": " ",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "speed": 1.0},
            "xi_api_key": self._api_key,
            "generation_config": {"chunk_length_schedule": [50, 90, 120, 150]},
        }
        if self._lexicon is not None:
            bos["pronunciation_dictionary_locators"] = [
                {"pronunciation_dictionary_id": self._lexicon[0], "version_id": self._lexicon[1]}
            ]
        await socket.send(json.dumps(bos))
        return socket

    def unavailable_reason(self) -> str | None:
        if key_configured(self._api_key):
            return None
        return "Voice is set to speak through ElevenLabs, but ELEVENLABS_API_KEY is not set."

    async def open(self, *, quality: str, lexicon_pls: str | None) -> ElevenLabsStream:
        model = self._quality if quality == "multilingual" else self._flash
        if lexicon_pls:
            try:
                await self.ensure_lexicon(lexicon_pls)
            except Exception as exc:
                logger.warning("elevenlabs_lexicon_failed", error=f"{type(exc).__name__}: {exc}")
        socket = await self.connect(model)
        logger.info("elevenlabs_connected", model=model)
        return ElevenLabsStream(self, socket, model=model)
