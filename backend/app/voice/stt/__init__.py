"""Speech-to-text providers behind one Protocol (11B).

* ``deepgram`` — Deepgram streaming with ``nova-3-medical`` (cloud backend).
* ``whisper`` — faster-whisper running locally (offline backend).

Both accept the corpus-derived boost vocabulary (11B.2): Deepgram as keyterms,
Whisper as decoding hotwords. The session never touches provider types.
"""

from __future__ import annotations

from app.config import Settings
from app.voice.stt.base import SttEvent, SttProvider, SttStream, SttWord


def get_stt_provider(settings: Settings) -> SttProvider:
    if settings.voice_backend == "cloud":
        from app.voice.stt.deepgram import DeepgramProvider

        return DeepgramProvider(
            api_key=settings.deepgram_api_key.get_secret_value(), model=settings.voice_stt_model
        )
    from app.voice.stt.whisper_local import WhisperProvider

    return WhisperProvider(
        model=settings.voice_whisper_model,
        compute_type=settings.voice_whisper_compute,
        cpu_threads=settings.voice_whisper_threads,
    )


__all__ = ["SttEvent", "SttProvider", "SttStream", "SttWord", "get_stt_provider"]
