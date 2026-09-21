"""Text-to-speech providers behind one Protocol (11F).

* ``elevenlabs`` — ElevenLabs streaming over a pre-warmed WebSocket; Flash
  for latency, Multilingual for prosody (a per-org setting).
* ``local`` — the operating system's synthesizer: Windows OneCore voices
  (WinRT) or espeak-ng on Linux. Real audio, no account.

Every stream yields 16 kHz PCM16 chunks and supports an immediate cancel.
"""

from __future__ import annotations

from app.config import Settings
from app.voice.tts.base import TtsProvider, TtsStream


def get_tts_provider(settings: Settings) -> TtsProvider:
    if settings.voice_backend == "cloud":
        from app.voice.tts.elevenlabs import ElevenLabsProvider

        return ElevenLabsProvider(
            api_key=settings.elevenlabs_api_key.get_secret_value(),
            voice_id=settings.voice_tts_voice_id,
            flash_model=settings.voice_tts_model,
            quality_model=settings.voice_tts_model_quality,
        )
    from app.voice.tts.local import LocalTtsProvider

    return LocalTtsProvider(voice=settings.voice_offline_voice)


__all__ = ["TtsProvider", "TtsStream", "get_tts_provider"]
