"""Deepgram Aura-2 text-to-speech over REST.

One Deepgram account covers both halves of voice mode: ``nova-3-medical``
hears the question (``app/voice/stt/deepgram.py``) and Aura-2 speaks the
answer. Deepgram gives new accounts $200 of credit with no card and no
expiry — at Aura-2's $0.030 per 1,000 characters that is millions of spoken
characters — which is what makes voice possible on the free deployment,
whose server is far too small to run a speech model of its own.

Each sentence is one ``POST /v1/speak`` asking for exactly what the browser
plays: raw 16-bit PCM at 16 kHz with no container (``encoding=linear16``,
``sample_rate=16000``, ``container=none``). The body is read as it arrives,
so audio reaches the playback worklet without waiting for the whole
sentence to be synthesized.

Two details that would otherwise corrupt or leak audio:

- PCM16 samples are two bytes and a network chunk may end half-way through
  one. Yielding it as-is shifts every later sample by a byte — speech turns
  into noise — so an odd trailing byte is carried into the next chunk.
- Cancel (barge-in) must stop the sound at once: the loop checks the flag
  between chunks and closing the response abandons the rest of the body.

Aura has no pronunciation-lexicon upload, so ``lexicon_pls`` is accepted and
ignored; drug names are read from their spelling.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import structlog

from app.voice.availability import CLOUD_SETUP, key_configured

logger = structlog.stdlib.get_logger("app.voice.tts.deepgram")

_SPEAK = "https://api.deepgram.com/v1/speak"
_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)


class DeepgramTtsStream:
    def __init__(self, client: httpx.AsyncClient, *, api_key: str, model: str) -> None:
        self._client = client
        self._api_key = api_key
        self.model = model
        self._cancelled = False
        self._lock = asyncio.Lock()

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        spoken = text.strip()
        if not spoken:
            return
        async with self._lock:
            self._cancelled = False
            params = {
                "model": self.model,
                "encoding": "linear16",
                "sample_rate": "16000",
                "container": "none",
            }
            try:
                async with self._client.stream(
                    "POST",
                    _SPEAK,
                    params=params,
                    json={"text": spoken},
                    headers={"Authorization": f"Token {self._api_key}"},
                ) as response:
                    if response.status_code != 200:
                        body = (await response.aread())[:300].decode("utf-8", "replace")
                        logger.warning(
                            "deepgram_tts_failed", status=response.status_code, body=body
                        )
                        return
                    carry = b""
                    async for chunk in response.aiter_bytes():
                        if self._cancelled:
                            return
                        data = carry + chunk
                        whole = len(data) - (len(data) % 2)
                        carry = data[whole:]
                        if whole:
                            yield data[:whole]
            except httpx.HTTPError as exc:
                logger.warning("deepgram_tts_unreachable", error=f"{type(exc).__name__}: {exc}")

    async def cancel(self) -> None:
        self._cancelled = True

    async def close(self) -> None:
        self._cancelled = True
        await self._client.aclose()


class DeepgramTtsProvider:
    name = "deepgram"

    def __init__(self, *, api_key: str, model: str = "aura-2-thalia-en") -> None:
        self._api_key = api_key
        self.model = model

    def unavailable_reason(self) -> str | None:
        return None if key_configured(self._api_key) else CLOUD_SETUP

    async def open(
        self, *, quality: str, lexicon_pls: str | None, voice: str | None = None
    ) -> DeepgramTtsStream:
        # One client per voice session: its connection pool keeps the TLS
        # session to Deepgram open between sentences.
        client = httpx.AsyncClient(timeout=_TIMEOUT, http2=False)
        return DeepgramTtsStream(client, api_key=self._api_key, model=voice or self.model)
