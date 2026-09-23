"""Process-wide voice dependencies (providers, vocabulary, lexicon, gates).

Built once per process on first use and hung on ``app.state`` — the STT/TTS
providers, the LASA table, the corpus vocabulary cache (with its refresh
check), the pronunciation lexicon derived from it, the completeness
classifier, and the endpoint policy. Sessions receive the runtime and never
construct providers themselves, which is what lets the tests swap in
scripted doubles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from app.config import Settings, get_settings
from app.guardrails.phi import PhiDetector
from app.voice.availability import provider_unavailable_reason
from app.voice.endpointing import (
    CompletenessClassifier,
    EndpointPolicy,
    HeuristicCompletenessClassifier,
    LLMCompletenessClassifier,
)
from app.voice.lasa import LasaTable, default_lasa_table
from app.voice.pronunciation import Lexicon, build_lexicon
from app.voice.stt import SttProvider, get_stt_provider
from app.voice.tts import TtsProvider, get_tts_provider
from app.voice.vocabulary import VocabularyCache, VoiceVocabulary

if TYPE_CHECKING:
    import asyncpg

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("app.voice.runtime")

BOOST_TERMS = 100


@dataclass(slots=True)
class VoiceRuntime:
    settings: Settings
    stt: SttProvider
    tts: TtsProvider
    lasa: LasaTable
    vocabulary_cache: VocabularyCache
    completeness: CompletenessClassifier
    endpoint_policy: EndpointPolicy
    # Regex-only detector for the inline (<5 ms) PHI scan on partials and
    # finals; the full detector (with Presidio telemetry) runs on commit and
    # is shared so the ~0.8 s engine load happens once per process.
    phi_inline: PhiDetector
    phi_full: PhiDetector = field(default_factory=PhiDetector)
    _lexicon: Lexicon | None = None
    _lexicon_signature: str | None = None

    async def vocabulary(self, pool: DbPool | None) -> VoiceVocabulary:
        return await self.vocabulary_cache.get(pool)

    def lexicon_for(self, vocabulary: VoiceVocabulary) -> Lexicon:
        if self._lexicon is None or self._lexicon_signature != vocabulary.corpus_signature:
            self._lexicon = build_lexicon(vocabulary)
            self._lexicon_signature = vocabulary.corpus_signature
            logger.info(
                "voice_lexicon_built",
                entries=len(self._lexicon.entries),
                coverage=round(self._lexicon.coverage, 3),
            )
        return self._lexicon

    @property
    def backend(self) -> str:
        return self.settings.voice_backend

    def unavailable_reason(self) -> str | None:
        """Why voice cannot run here, or None when both providers can serve."""
        return provider_unavailable_reason(self.stt) or provider_unavailable_reason(self.tts)

    def warm_guardrails(self) -> None:
        """Load the Presidio engine (blocking; call it off the event loop)."""
        self.phi_full.scan("warm-up")


def build_runtime(settings: Settings | None = None) -> VoiceRuntime:
    resolved = settings if settings is not None else get_settings()
    lasa = default_lasa_table()
    completeness: CompletenessClassifier = (
        LLMCompletenessClassifier()
        if resolved.ai_backend == "cloud"
        else HeuristicCompletenessClassifier()
    )
    runtime = VoiceRuntime(
        settings=resolved,
        stt=get_stt_provider(resolved),
        tts=get_tts_provider(resolved),
        lasa=lasa,
        vocabulary_cache=VocabularyCache(lasa),
        completeness=completeness,
        endpoint_policy=EndpointPolicy(
            base_ms=resolved.voice_endpoint_silence_ms,
            extended_ms=resolved.voice_endpoint_extended_ms,
            ceiling_ms=resolved.voice_endpoint_ceiling_ms,
        ),
        phi_inline=PhiDetector(use_presidio=False),
    )
    logger.info(
        "voice_runtime_ready" if runtime.unavailable_reason() is None else "voice_unavailable",
        backend=resolved.voice_backend,
        stt=f"{runtime.stt.name}:{runtime.stt.model}",
        tts=runtime.tts.name,
        lasa_pairs=len(lasa),
    )
    return runtime


def get_voice_runtime(app_state: Any) -> VoiceRuntime:
    runtime = getattr(app_state, "voice_runtime", None)
    if not isinstance(runtime, VoiceRuntime):
        runtime = build_runtime()
        app_state.voice_runtime = runtime
    return runtime
