"""Phase 11 — the voice agent.

A cascaded pipeline (STT → guardrails → RAG graph → TTS) over a single
WebSocket per session. Every module here is either a pure component with its
own tests (state machine, endpointing, correction, LASA gate, rendering,
segmentation, waterfall) or a thin provider adapter behind a Protocol
(``stt``/``tts``), so the orchestration is exercised end-to-end without a
paid key and the cloud providers are swapped in by configuration.

See ``docs/ARCHITECTURE.md`` for the cascade-vs-speech-to-speech decision.
"""
