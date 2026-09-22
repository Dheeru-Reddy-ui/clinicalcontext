"""Public sign-up: an account without Supabase sending anything.

The deployment's first real sign-up failed with ``500 Error sending
confirmation email`` — Supabase's built-in mailer refuses every address
outside the project team. These tests pin the behaviour that replaced it:
the account is created through the admin API, a duplicate is named as such,
and the endpoint is not a free account-creation faucet.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from app.services.signup import SignupService
from tests.conftest import ApiEnv

_RL_KEYS = (
    "rl:signup:testclient",
    "rl:signup:127.0.0.1",
    "rl:anon:testclient",
    "rl:anon:127.0.0.1",
)


@pytest.fixture(autouse=True)
async def _clean_limits(env: ApiEnv) -> AsyncIterator[None]:
    async def clear() -> None:
        await env.redis.delete(*_RL_KEYS)
        async for key in env.redis.scan_iter("rl:signup:e:*"):
            await env.redis.delete(key)

    await clear()
    yield
    await clear()


def _service_returning(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[], SignupService]:
    """A SignupService whose upstream is `handler` rather than Supabase."""

    def factory() -> SignupService:
        return SignupService(httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    return factory


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"id": "00000000-0000-4000-8000-00000000abcd"})


# -- the service's mapping of what Supabase says ---------------------------------------


async def test_the_created_user_is_confirmed_and_carries_the_name() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        seen["auth"] = request.headers.get("authorization", "")
        return _ok(request)

    service = SignupService(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await service.create_user(
        email="new@example.org", password="LongEnough123!", full_name="Dr New"
    )

    assert str(seen["url"]).endswith("/auth/v1/admin/users")
    body = json.loads(str(seen["body"]))
    # Confirmed at creation: no email is sent, and the account can sign in.
    assert body["email_confirm"] is True
    assert body["user_metadata"]["full_name"] == "Dr New"
    # The admin API is the whole point: it must be the service-role key.
    assert str(seen["auth"]).startswith("Bearer ")


@pytest.mark.parametrize(
    "payload",
    [
        {"error_code": "email_exists", "msg": "already registered"},
        {"msg": "A user with this email address has already been registered"},
    ],
)
async def test_a_taken_address_is_named_as_such(payload: dict[str, str]) -> None:
    from app.core.errors import EmailExistsError

    transport = httpx.MockTransport(lambda _r: httpx.Response(422, json=payload))
    service = SignupService(httpx.AsyncClient(transport=transport))
    with pytest.raises(EmailExistsError):
        await service.create_user(
            email="taken@example.org", password="LongEnough123!", full_name="X"
        )


async def test_supabases_own_complaint_about_a_weak_password_is_passed_through() -> None:
    from app.core.errors import InvalidRequestError

    payload = {
        "error_code": "weak_password",
        "msg": "Password should contain at least one symbol",
    }
    transport = httpx.MockTransport(lambda _r: httpx.Response(422, json=payload))
    service = SignupService(httpx.AsyncClient(transport=transport))
    with pytest.raises(InvalidRequestError, match="at least one symbol"):
        await service.create_user(email="new@example.org", password="password", full_name="X")


async def test_an_upstream_outage_is_a_503_not_a_crash() -> None:
    from app.core.errors import ServiceUnavailableError

    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    service = SignupService(httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    with pytest.raises(ServiceUnavailableError):
        await service.create_user(email="new@example.org", password="LongEnough123!", full_name="X")


# -- the endpoint ----------------------------------------------------------------------


async def test_signup_creates_an_account_without_sending_email(
    env: ApiEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.api.public.SignupService", _service_returning(_ok))
    response = await env.client.post(
        "/api/public/signup",
        json={
            "email": "Fresh@Example.org",
            "password": "LongEnough123!",
            "full_name": "Dr Fresh",
        },
    )
    assert response.status_code == 200, response.text
    # Normalised, so "Fresh@" and "fresh@" are one account.
    assert response.json()["email"] == "fresh@example.org"


async def test_a_duplicate_signup_is_a_409_the_form_can_branch_on(
    env: ApiEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    def taken(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error_code": "email_exists", "msg": "already registered"})

    monkeypatch.setattr("app.api.public.SignupService", _service_returning(taken))
    response = await env.client.post(
        "/api/public/signup",
        json={"email": "taken@example.org", "password": "LongEnough123!", "full_name": "Dr Taken"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_exists"


async def test_a_short_password_never_reaches_supabase(
    env: ApiEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(_request: httpx.Request) -> httpx.Response:  # pragma: no cover — must not run
        raise AssertionError("upstream called for a password the schema should have rejected")

    monkeypatch.setattr("app.api.public.SignupService", _service_returning(explode))
    response = await env.client.post(
        "/api/public/signup",
        json={"email": "new@example.org", "password": "short", "full_name": "Dr Short"},
    )
    assert response.status_code == 422


async def test_signup_is_rate_limited_per_address(
    env: ApiEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.api.public.SignupService", _service_returning(_ok))
    body = {"email": "flood@example.org", "password": "LongEnough123!", "full_name": "Dr Flood"}
    statuses = [
        (await env.client.post("/api/public/signup", json=body)).status_code for _ in range(5)
    ]
    # Three a minute for one address: the fourth attempt is refused.
    assert statuses[:3] == [200, 200, 200], statuses
    assert statuses[3] == 429, statuses
