"""Password sign-up, performed server-side against Supabase's admin API.

Why this exists rather than the browser calling ``supabase.auth.signUp``:
that path makes Supabase send a confirmation email, and Supabase's built-in
mailer only delivers to the project team's own addresses (two an hour).
Every other would-be user got ``500 Error sending confirmation email`` and no
account. Creating the user through the admin API sends nothing, so sign-up
works with no mail provider configured at all.

The trade-off is explicit: an address is not proven to belong to whoever
typed it. That is the same posture as Supabase's own "confirm email" switch
turned off, and it is acceptable here because the product holds no patient
data and an account grants nothing but an empty organisation. To require
verification instead, configure SMTP in Supabase and have the sign-up form
call ``supabase.auth.signUp`` directly again (see docs/DEPLOY.md, Part 2);
this endpoint is then dead code and should go.

Password reset genuinely cannot be done without email, and is left on
Supabase's own flow.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.config import get_settings
from app.core.errors import EmailExistsError, InvalidRequestError, ServiceUnavailableError
from app.core.supabase_keys import service_headers

logger = structlog.stdlib.get_logger("app.signup")

_TIMEOUT = httpx.Timeout(10.0)

# GoTrue's way of saying the address is taken. The status alone is not
# enough: 422 also carries weak-password and malformed-address complaints.
_EXISTS_CODES = {"email_exists", "user_already_exists"}
_EXISTS_TEXT = "already been registered"


class SignupService:
    """Creates confirmed Supabase users with the service-role key."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def create_user(self, *, email: str, password: str, full_name: str) -> None:
        settings = get_settings()
        url = f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users"
        payload = {
            "email": email,
            "password": password,
            # No email is sent either way; this marks the address confirmed so
            # the account can sign in at once. full_name rides in
            # user_metadata exactly where the old client-side sign-up put it,
            # so the profile bootstrap still finds it.
            "email_confirm": True,
            "user_metadata": {"full_name": full_name},
        }
        # The admin API needs the secret key, in the header its generation
        # expects (app/core/supabase_keys.py).
        headers = service_headers(settings)
        try:
            if self._client is not None:
                response = await self._client.post(url, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("signup_upstream_unreachable", error=str(exc))
            raise ServiceUnavailableError(
                "The sign-in service could not be reached. Try again in a moment."
            ) from exc

        if response.status_code < 300:
            return
        self._raise_for(response)

    @staticmethod
    def _raise_for(response: httpx.Response) -> None:
        body: dict[str, Any] = {}
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                body = parsed
        except ValueError:
            pass
        code = str(body.get("error_code") or body.get("code") or "")
        message = str(body.get("msg") or body.get("message") or "").strip()

        if code in _EXISTS_CODES or _EXISTS_TEXT in message.lower():
            raise EmailExistsError()
        if response.status_code in (400, 422):
            # Supabase's own words — a weak password, a rejected address.
            raise InvalidRequestError(message or "The email or password was rejected.")
        logger.error(
            "signup_upstream_failed", status_code=response.status_code, upstream_message=message
        )
        raise ServiceUnavailableError(
            "The sign-in service could not create the account. Try again in a moment."
        )
