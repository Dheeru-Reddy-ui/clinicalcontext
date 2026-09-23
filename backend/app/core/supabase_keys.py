"""Headers for Supabase's server-side APIs, with either generation of key.

Supabase has two kinds of key, and they are sent differently:

- **Legacy** ``anon`` / ``service_role`` keys are long-lived JWTs (``eyJ…``,
  three dot-separated segments). They may be sent as ``apikey`` and as
  ``Authorization: Bearer``.
- **New** ``sb_publishable_…`` / ``sb_secret_…`` keys are short strings, not
  JWTs. Supabase's rule is to send them on ``apikey`` only: anything that
  tries to verify one as a bearer token fails.

This mattered on the first deployment. The code sent the secret key as a
bearer token, which the local Supabase CLI's legacy keys accept, so every
local test passed; the production project had new keys, and every
server-side call was refused with "invalid JWT … invalid number of
segments". Sign-up answered 503 to everyone.

Supabase is retiring the legacy keys by the end of 2026, so the new form is
the one that has to keep working.
"""

from __future__ import annotations

from app.config import Settings, get_settings


def is_jwt(key: str) -> bool:
    """A legacy key is a JWT: header.payload.signature."""
    return key.count(".") == 2


def service_headers(settings: Settings | None = None) -> dict[str, str]:
    """Headers that authenticate as the project's secret (service) key.

    The key always goes on ``apikey``. It goes on ``Authorization`` as well
    only when it is a legacy JWT; a new ``sb_secret_`` key there is refused.
    """
    key = (settings or get_settings()).supabase_service_role_key.get_secret_value()
    headers = {"apikey": key}
    if is_jwt(key):
        headers["Authorization"] = f"Bearer {key}"
    return headers
