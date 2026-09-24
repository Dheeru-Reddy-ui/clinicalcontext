"""Application configuration via Pydantic Settings.

Every environment variable the backend consumes is declared here — nothing else
in the codebase reads ``os.environ``. ``get_settings()`` is invoked at process
start (module import in ``app.main``), so a missing required variable aborts
boot immediately with a ``ValidationError`` naming the variable.

Fail-fast contract: all variables below without a default are required at boot.
Keys for services wired in later phases must still be present (see
``.env.example``); placeholder values are acceptable until the phase that
actually calls that service.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_DIR.parent


class Settings(BaseSettings):
    """Runtime configuration. See ``.env.example`` for documentation of each value."""

    model_config = SettingsConfigDict(
        # Later files take priority: backend/.env overrides the repo-root .env.
        env_file=(_REPO_ROOT / ".env", _BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # Values arrive by paste — into a dashboard field, a .env, a secret
        # store — and a trailing newline survives all three. One rode into
        # SUPABASE_ANON_KEY on Render and broke sign-up in production: httpx
        # refuses to build a header containing a newline, so the request to
        # Supabase was never sent and every sign-up answered 503. A stray
        # newline is never part of a key, a URL or a DSN, so strip it here
        # once rather than at each use.
        str_strip_whitespace=True,
    )

    # -- Database pool ---------------------------------------------------------
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10
    # Supabase's transaction-mode pooler (port 6543) multiplexes one server
    # connection across clients, so a prepared statement made on one client's
    # behalf can be reused on another's — asyncpg's statement cache then fails
    # with "prepared statement _pgN already exists". Detected from the DSN and
    # overridable here.
    db_disable_statement_cache: bool | None = None
    # How many candidates the HNSW index considers per search. The default
    # (40) is below the 50 candidates retrieval asks for, and the tenant
    # filter is applied *after* the index, so the search came back short.
    # Measured on the seeded corpus: 100 returns the full candidate set in
    # ~4 ms; 200 and 400 cost more for the same results.
    hnsw_ef_search: int = 100

    @property
    def db_uses_transaction_pooler(self) -> bool:
        """True when DATABASE_URL points at a transaction-mode pooler."""
        if self.db_disable_statement_cache is not None:
            return self.db_disable_statement_cache
        dsn = self.database_url
        return ":6543/" in dsn or "pgbouncer=true" in dsn or "pooler.supabase.com" in dsn

    # -- Runtime ---------------------------------------------------------------
    environment: Literal["local", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # AI backend for the agent graph: "offline" uses the deterministic
    # heuristic reasoner + local embedder/reranker (no paid keys); "cloud"
    # uses Claude + Cohere. Defaults to offline so the system runs without keys.
    ai_backend: Literal["offline", "cloud"] = "offline"
    langsmith_project: str = "clinicalcontext"

    # -- Observability (Phase 13) ----------------------------------------------
    # The release stamped on every trace, error and log line: the deploy sets
    # it to the git SHA; locally it stays "dev". Render exposes the deployed
    # commit as RENDER_GIT_COMMIT, so a deploy there needs no extra step to
    # stamp itself — and /health reports it, which is how the pipeline waits
    # for the *new* release rather than the old one still answering.
    release: str = Field(
        default="dev", validation_alias=AliasChoices("RELEASE", "RENDER_GIT_COMMIT")
    )
    # OpenTelemetry: traces go to an OTLP/HTTP endpoint when one is configured
    # (a collector, Jaeger, Honeycomb, …); "console" prints finished spans;
    # empty leaves the tracer provider installed but exporting nothing, so
    # the same spans and attributes exist in every environment.
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "clinicalcontext-api"
    # Sentry: off without a DSN. Events carry the release and the request id.
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.0
    # Weekly digest email (Phase 13): SMTP without auth is the local mailpit;
    # empty host means in-app only.
    smtp_host: str = ""
    smtp_port: int = 1025
    smtp_from: str = "ClinicalContext <digest@clinicalcontext.local>"
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_starttls: bool = False
    app_public_url: str = "http://localhost:3000"
    # Webhook deliveries are queued by API requests, so the API is always
    # awake when there is something to deliver. On a platform with a separate
    # worker process this stays 0 and the worker drains the queue; on one
    # without (a free tier that sleeps when idle), a positive interval runs
    # the drain inside the API process instead.
    webhook_drain_interval_seconds: int = 0

    # -- HTTP ------------------------------------------------------------------
    cors_origins: str = Field(
        default="http://localhost:3000",
        description="Comma-separated list of allowed browser origins.",
    )

    # -- Data stores (exercised from Phase 1) -----------------------------------
    database_url: str = Field(
        description="Postgres DSN. Local: the docker-compose instance. Deployed: Supabase."
    )
    redis_url: str = Field(
        description="Redis URL. Local: the docker-compose instance. Deployed: Upstash."
    )

    # -- Supabase (wired in Phase 2: auth, RLS, storage) -------------------------
    supabase_url: str

    @field_validator("supabase_url")
    @classmethod
    def _normalise_supabase_url(cls, value: str) -> str:
        """The project URL to the host, whatever box it was copied from.

        The dashboard shows the REST endpoint (``…/rest/v1``) far more
        prominently than the bare project URL. With the suffix left on, the
        JWKS URL becomes ``…/rest/v1/auth/v1/.well-known/jwks.json`` and
        every token fails verification — seen on the first deployment.
        """
        value = value.strip().rstrip("/")
        for suffix in ("/rest/v1", "/auth/v1", "/storage/v1", "/realtime/v1", "/functions/v1"):
            if value.endswith(suffix):
                value = value[: -len(suffix)]
        return value.rstrip("/")

    supabase_anon_key: SecretStr
    supabase_service_role_key: SecretStr
    # Optional: only for legacy Supabase projects that still sign JWTs with the
    # shared HS256 secret. Projects on asymmetric signing keys (the default for
    # new projects) are verified via JWKS and leave this unset.
    supabase_jwt_secret: SecretStr | None = None
    # Local development only. When auth (GoTrue) runs in a *separate* database
    # from the app's data (Supabase CLI for auth + the compose Postgres with
    # its auth-schema shim for data), a verified identity has no auth.users row
    # in the data DB, so provisioning mirrors it there first. On a real
    # Supabase project auth and data share one database and GoTrue owns
    # auth.users — leave this off; nothing here ever writes that table.
    auth_identity_mirror: bool = False

    @property
    def supabase_issuer(self) -> str:
        """The `iss` claim Supabase stamps on its JWTs."""
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def supabase_jwks_url(self) -> str:
        return f"{self.supabase_issuer}/.well-known/jwks.json"

    # -- AI providers (wired from Phase 3 onward) --------------------------------
    cohere_api_key: SecretStr
    anthropic_api_key: SecretStr
    langsmith_api_key: SecretStr

    # -- Free language models (the chat assistant, and the graph when set) --------
    # Any OpenAI-compatible chat endpoint. Each provider is used only when its
    # key is real; they are tried in LLM_PROVIDER_ORDER and a provider that
    # refuses (rate limit, quota, outage) hands the request to the next, so a
    # free tier's daily cap degrades to the next free tier rather than to an
    # error. With none configured, answers come from the offline engine.
    llm_provider_order: str = "groq,cerebras,openai_compat"
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "openai/gpt-oss-120b"
    # Groq's free limits are per model: the smaller model is a second quota.
    groq_fallback_model: str = "openai/gpt-oss-20b"
    cerebras_api_key: SecretStr = SecretStr("")
    cerebras_model: str = "gpt-oss-120b"
    # A third, generic slot: OpenRouter, a self-hosted vLLM/Ollama, anything
    # speaking the OpenAI chat API.
    openai_compat_base_url: str = ""
    openai_compat_api_key: SecretStr = SecretStr("")
    openai_compat_model: str = ""
    llm_timeout_seconds: float = 45.0

    # -- Voice pipeline (Phase 11) ----------------------------------------------
    deepgram_api_key: SecretStr
    elevenlabs_api_key: SecretStr
    # "offline": faster-whisper (STT) + the operating system's speech
    # synthesizer (TTS) — real engines, no paid keys, so the whole cascade
    # runs and is measured locally. "cloud": Deepgram nova-3-medical +
    # ElevenLabs Flash. The pipeline (guardrails, speculation, LASA gate,
    # rendering, barge-in) is identical; only the two edge providers differ.
    voice_backend: Literal["offline", "cloud"] = "offline"
    voice_stt_model: str = "nova-3-medical"
    voice_tts_model: str = "eleven_flash_v2_5"
    voice_tts_model_quality: str = "eleven_multilingual_v2"
    voice_tts_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    # In the cloud backend, which provider speaks. Deepgram by default: one
    # free Deepgram account then covers both hearing and speaking.
    voice_tts_provider: Literal["deepgram", "elevenlabs"] = "deepgram"
    voice_deepgram_tts_model: str = "aura-2-thalia-en"
    # Offline engines.
    voice_whisper_model: str = "tiny.en"
    voice_whisper_compute: str = "int8"
    voice_whisper_threads: int = 0
    voice_offline_voice: str = "Microsoft George"
    # Turn-taking (11C): base silence threshold, the extension granted when
    # the utterance looks incomplete, and the hard ceiling that always commits.
    voice_endpoint_silence_ms: int = 300
    voice_endpoint_extended_ms: int = 1500
    voice_endpoint_ceiling_ms: int = 2000
    # Speculative retrieval triggers (11D.1) and the final-vs-speculated
    # similarity that counts as a hit.
    voice_speculation_min_words: int = 6
    voice_speculation_stable_ms: int = 300
    voice_speculation_similarity: float = 0.9
    # LASA confirmation gate (11D.3): STT word confidence below this floor on
    # a confusable drug name forces a spoken confirmation.
    voice_lasa_confidence_floor: float = 0.6
    # Earcon after this much PROCESSING (11F.4); the verbal mask only past this.
    voice_earcon_after_ms: int = 800
    voice_mask_after_ms: int = 2000

    # -- NCBI E-utilities (optional: raises the rate limit 3 -> 10 req/s and
    # identifies the client politely; ingestion works without either) ------------
    ncbi_api_key: SecretStr | None = None
    ncbi_email: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        """The CORS origins as a parsed list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton (validated once, cached)."""
    return Settings()
