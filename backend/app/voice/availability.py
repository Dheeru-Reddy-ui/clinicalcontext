"""Can voice run on this server? Asked of each provider, said in plain words.

On the first deployment the voice page opened, the browser connected, and
the session died on start: the free image carries no speech engine
(faster-whisper and espeak-ng are left out to fit 512 MB), while
``/voice/config`` went on reporting a ready runtime. Each provider now
answers ``unavailable_reason()`` — ``None`` when it can serve, otherwise a
sentence a person can act on — and the config endpoint and the WebSocket
both use it, so the page says why instead of silently not recording.

Providers without the method (the scripted doubles the tests inject) count
as available.
"""

from __future__ import annotations

from typing import Any

# Values the deployment uses for a key it does not have: required at boot,
# never valid at a provider.
_PLACEHOLDERS = {"", "placeholder", "changeme", "none", "ci"}


def key_configured(key: str) -> bool:
    value = key.strip().lower()
    return value not in _PLACEHOLDERS and not value.startswith(("ci-placeholder", "test-"))


def provider_unavailable_reason(provider: Any) -> str | None:
    check = getattr(provider, "unavailable_reason", None)
    return check() if callable(check) else None


CLOUD_SETUP = (
    "Voice uses Deepgram for speech: create a free account at deepgram.com "
    "(no card), make an API key, and set DEEPGRAM_API_KEY and VOICE_BACKEND=cloud "
    "on the API service. docs/DEPLOY.md, Part 4, has the steps."
)
