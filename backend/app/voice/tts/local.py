"""Offline TTS: the operating system's synthesizer (the ``offline`` backend).

* Windows: OneCore voices through WinRT ``Windows.Media.SpeechSynthesis``
  (in-process, ~200-300 ms per sentence, 16 kHz WAV out; includes the en-IN
  voices Heera and Ravi used for the accent fixtures).
* Linux/macOS: ``espeak-ng`` on PATH (22.05 kHz WAV, resampled to 16 kHz).

Neither engine streams within a sentence, so a sentence is synthesized whole
and then yielded in 60 ms chunks; time-to-first-byte is the synthesis time —
reported as such, never hidden. Pronunciation uses respellings (no lexicon
support), applied only to the text sent to the engine.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import sys
from collections.abc import AsyncIterator
from typing import Any

import structlog

from app.voice.audio import read_wav, resample_pcm16

logger = structlog.stdlib.get_logger("app.voice.tts.local")

_CHUNK_BYTES = 16_000 * 2 * 60 // 1000  # 60 ms


async def synthesize_winrt(text: str, voice_name: str) -> bytes:
    """→ 16 kHz PCM16 via Windows OneCore voices."""
    # faster-whisper (CTranslate2/onnxruntime) must be imported before the
    # WinRT projection in this process, or the process dies on the next
    # native call — observed on Windows 11 during Phase 11 bring-up.
    with contextlib.suppress(ImportError):
        import faster_whisper  # noqa: F401
    from winrt.windows.media.speechsynthesis import SpeechSynthesizer
    from winrt.windows.storage.streams import DataReader

    synthesizer = SpeechSynthesizer()
    voices = list(SpeechSynthesizer.all_voices)
    chosen = next((v for v in voices if voice_name.lower() in v.display_name.lower()), None)
    if chosen is not None:
        synthesizer.voice = chosen
    stream = await synthesizer.synthesize_text_to_stream_async(text)
    reader = DataReader(stream.get_input_stream_at(0))
    size = int(stream.size)
    await reader.load_async(size)
    buffer = bytearray(size)
    reader.read_bytes(buffer)
    pcm, rate = read_wav(bytes(buffer))
    return resample_pcm16(pcm, rate)


def winrt_voices() -> list[tuple[str, str]]:
    """(display_name, language) for every installed OneCore voice."""
    with contextlib.suppress(ImportError):
        import faster_whisper  # noqa: F401
    from winrt.windows.media.speechsynthesis import SpeechSynthesizer

    return [(v.display_name, v.language) for v in SpeechSynthesizer.all_voices]


async def synthesize_espeak(text: str, voice: str = "en-gb") -> bytes:
    binary = shutil.which("espeak-ng") or shutil.which("espeak")
    if binary is None:
        raise RuntimeError("espeak-ng is not installed (apt-get install espeak-ng)")
    process = await asyncio.create_subprocess_exec(
        binary,
        "--stdout",
        "-v",
        voice,
        "-s",
        "170",
        text,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    wav, _ = await process.communicate()
    pcm, rate = read_wav(wav)
    return resample_pcm16(pcm, rate)


_IS_WINDOWS = sys.platform.startswith("win")


async def synthesize(text: str, voice: str) -> bytes:
    if _IS_WINDOWS:
        return await synthesize_winrt(text, voice)
    espeak_voice = "en-gb" if voice.startswith("Microsoft") else voice
    return await synthesize_espeak(text, espeak_voice)


class LocalTtsStream:
    def __init__(self, voice: str, *, respell: Any | None = None) -> None:
        self._voice = voice
        self._respell = respell
        self.model = f"local:{voice}"
        self._cancelled = False
        self._current: asyncio.Task[bytes] | None = None

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        self._cancelled = False
        spoken = self._respell(text) if self._respell is not None else text
        self._current = asyncio.create_task(synthesize(spoken, self._voice))
        try:
            pcm = await self._current
        except asyncio.CancelledError:
            return
        finally:
            self._current = None
        for offset in range(0, len(pcm), _CHUNK_BYTES):
            if self._cancelled:
                return
            yield pcm[offset : offset + _CHUNK_BYTES]

    async def cancel(self) -> None:
        self._cancelled = True
        if self._current is not None and not self._current.done():
            self._current.cancel()

    async def close(self) -> None:
        await self.cancel()


class LocalTtsProvider:
    name = "local"

    def __init__(self, *, voice: str = "Microsoft George") -> None:
        self._voice = voice
        self._respell: Any | None = None

    def unavailable_reason(self) -> str | None:
        if _IS_WINDOWS:
            import importlib.util

            if importlib.util.find_spec("winrt") is not None:
                return None
        elif shutil.which("espeak-ng") or shutil.which("espeak"):
            return None
        from app.voice.availability import CLOUD_SETUP

        return "This server has no speech synthesizer installed. " + CLOUD_SETUP

    def set_respeller(self, respell: Any) -> None:
        self._respell = respell

    async def open(
        self, *, quality: str, lexicon_pls: str | None, voice: str | None = None
    ) -> LocalTtsStream:
        # The operating system's voice; the listener's Aura-2 choice does not apply.
        # Warm the engine: the first WinRT synthesis in a process is slow.
        try:
            await synthesize("Ready.", self._voice)
        except Exception as exc:
            logger.warning("local_tts_warmup_failed", error=f"{type(exc).__name__}: {exc}")
        return LocalTtsStream(self._voice, respell=self._respell)
