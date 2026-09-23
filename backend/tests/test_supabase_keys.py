"""Which header a Supabase key goes on depends on its generation.

The first deployment's project had the new ``sb_secret_`` keys; the code
sent the secret key as a bearer token, which only the legacy JWT keys
accept, and every server-side call was refused — sign-up answered 503 to
everyone while local tests, on the CLI's legacy keys, all passed.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.core.supabase_keys import is_jwt, service_headers

# Only the shape matters (three dot-separated segments), so the fake legacy
# key is built from placeholders: a realistic-looking JWT literal here would
# rightly trip the secret scanner.
LEGACY = ".".join(["header", "payload", "signature"])
NEW = "placeholder-secret-new-style"


def _settings(monkeypatch: pytest.MonkeyPatch, key: str) -> Settings:
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", key)
    return Settings(_env_file=None)


def test_a_legacy_key_is_a_jwt_and_a_new_one_is_not() -> None:
    assert is_jwt(LEGACY)
    assert not is_jwt(NEW)
    assert not is_jwt("sb_secret_abc")


def test_a_legacy_service_key_goes_on_both_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    headers = service_headers(_settings(monkeypatch, LEGACY))
    assert headers == {"apikey": LEGACY, "Authorization": f"Bearer {LEGACY}"}


def test_a_new_secret_key_goes_on_apikey_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # Supabase: "Send publishable and secret keys on the apikey header, not
    # on Authorization: Bearer." As a bearer token it fails JWT parsing.
    headers = service_headers(_settings(monkeypatch, NEW))
    assert headers == {"apikey": NEW}
    assert "Authorization" not in headers
