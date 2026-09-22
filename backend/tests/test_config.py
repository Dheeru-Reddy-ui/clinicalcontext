"""Settings: fail-fast on missing required vars, correct parsing otherwise."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings

REQUIRED_ENV_VARS = [
    "DATABASE_URL",
    "REDIS_URL",
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "COHERE_API_KEY",
    "ANTHROPIC_API_KEY",
    "LANGSMITH_API_KEY",
    "DEEPGRAM_API_KEY",
    "ELEVENLABS_API_KEY",
]


def test_missing_required_vars_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in REQUIRED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    missing_fields = {str(error["loc"][0]) for error in excinfo.value.errors()}
    assert missing_fields == {var.lower() for var in REQUIRED_ENV_VARS}


def test_settings_load_from_environment() -> None:
    settings = Settings(_env_file=None)
    assert settings.environment == "local"
    assert settings.database_url.startswith("postgresql://")
    assert settings.redis_url.startswith("redis://")


def test_cors_origins_parse_to_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000, https://app.example.com ,")
    settings = Settings(_env_file=None)
    assert settings.cors_origin_list == ["http://localhost:3000", "https://app.example.com"]


def test_api_keys_are_secret_and_never_leak_in_repr() -> None:
    settings = Settings(_env_file=None)
    assert isinstance(settings.anthropic_api_key, SecretStr)
    rendered = repr(settings) + str(settings)
    assert settings.anthropic_api_key.get_secret_value() not in rendered
    assert settings.supabase_service_role_key.get_secret_value() not in rendered


def test_transaction_pooler_is_detected_from_the_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncpg's prepared-statement cache is unsafe behind a transaction-mode
    pooler, and the failure it produces ("prepared statement _pg1 already
    exists") appears only under concurrency in production. So the pooler is
    detected from the DSN rather than remembered by a human."""
    from app.config import Settings

    direct = Settings(database_url="postgresql://u:p@db.project.supabase.co:5432/postgres")
    assert direct.db_uses_transaction_pooler is False

    for dsn in (
        "postgresql://u:p@aws-0-eu-west-2.pooler.supabase.com:6543/postgres",
        "postgresql://u:p@db.project.supabase.co:6543/postgres",
        "postgresql://u:p@host:5432/postgres?pgbouncer=true",
    ):
        assert Settings(database_url=dsn).db_uses_transaction_pooler is True, dsn

    # And it can always be forced either way.
    forced = Settings(
        database_url="postgresql://u:p@localhost:5432/postgres",
        db_disable_statement_cache=True,
    )
    assert forced.db_uses_transaction_pooler is True


def test_release_falls_back_to_the_platform_commit_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Render stamps RENDER_GIT_COMMIT on every deploy; without a RELEASE of
    its own the app adopts it, so traces, errors and /health name the real
    revision. An explicit RELEASE still wins."""
    monkeypatch.delenv("RELEASE", raising=False)
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    assert Settings(_env_file=None).release == "dev"

    monkeypatch.setenv("RENDER_GIT_COMMIT", "0123abcd0123abcd0123abcd0123abcd0123abcd")
    assert Settings(_env_file=None).release == "0123abcd0123abcd0123abcd0123abcd0123abcd"

    monkeypatch.setenv("RELEASE", "v9")
    assert Settings(_env_file=None).release == "v9"


def test_supabase_url_is_normalised_to_the_project_host() -> None:
    """The dashboard's REST endpoint is what people copy; a trailing slash is
    the other common slip. Both must yield the bare project URL, or the JWKS
    endpoint is built on the wrong path and every token fails."""
    for raw in (
        "https://abc.supabase.co",
        "https://abc.supabase.co/",
        "https://abc.supabase.co/rest/v1",
        "https://abc.supabase.co/rest/v1/",
        " https://abc.supabase.co/auth/v1 ",
    ):
        settings = Settings(_env_file=None, supabase_url=raw)
        assert settings.supabase_url == "https://abc.supabase.co", raw
        assert settings.supabase_jwks_url == "https://abc.supabase.co/auth/v1/.well-known/jwks.json"
