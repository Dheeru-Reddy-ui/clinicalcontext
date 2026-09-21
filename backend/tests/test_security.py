"""JWT verifier unit tests — every rejection path, no database, no network."""

from __future__ import annotations

from uuid import uuid4

import jwt
import pytest

from app.config import Settings
from app.core.errors import AuthError
from app.core.security import JWKSCache, JWTVerifier
from tests.conftest import mint_token, test_jwks

_ISSUER = "https://example.supabase.co/auth/v1"


def _verifier(**settings_overrides: str) -> JWTVerifier:
    settings = Settings(_env_file=None, **settings_overrides)  # type: ignore[call-arg]
    return JWTVerifier(
        settings,
        jwks=JWKSCache("unused://in-tests", preloaded=test_jwks()),
    )


async def test_valid_token_yields_auth_context() -> None:
    user_id, org_id = uuid4(), uuid4()
    token = mint_token(sub=user_id, email="doc@example.org", full_name="Dr. Example", org_id=org_id)

    context = await _verifier().verify(token)

    assert context.user_id == user_id
    assert context.email == "doc@example.org"
    assert context.full_name == "Dr. Example"
    assert context.org_claim == org_id


async def test_org_claim_absent_is_none() -> None:
    context = await _verifier().verify(mint_token(sub=uuid4(), email="x@y.z"))
    assert context.org_claim is None


async def test_expired_token_rejected() -> None:
    token = mint_token(sub=uuid4(), expires_in=-120)
    with pytest.raises(AuthError, match="expired"):
        await _verifier().verify(token)


async def test_wrong_audience_rejected() -> None:
    token = mint_token(sub=uuid4(), audience="anon")
    with pytest.raises(AuthError, match="invalid token"):
        await _verifier().verify(token)


async def test_wrong_issuer_rejected() -> None:
    token = mint_token(sub=uuid4(), issuer="https://attacker.example.com/auth/v1")
    with pytest.raises(AuthError, match="invalid token"):
        await _verifier().verify(token)


async def test_unknown_signing_key_rejected() -> None:
    token = mint_token(sub=uuid4(), kid="rotated-away-key")
    with pytest.raises(AuthError, match="unknown key"):
        await _verifier().verify(token)


async def test_garbage_token_rejected() -> None:
    with pytest.raises(AuthError, match="malformed"):
        await _verifier().verify("not-a-jwt-at-all")


async def test_non_uuid_subject_rejected() -> None:
    token = mint_token(sub="service-account-7")
    with pytest.raises(AuthError, match="subject"):
        await _verifier().verify(token)


async def test_hs256_rejected_without_shared_secret() -> None:
    token = jwt.encode(
        {"sub": str(uuid4()), "aud": "authenticated", "iss": _ISSUER, "exp": 2**31},
        "some-secret",
        algorithm="HS256",
    )
    with pytest.raises(AuthError, match="HS256"):
        await _verifier().verify(token)


async def test_hs256_accepted_with_configured_secret() -> None:
    secret = "legacy-project-jwt-secret"
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "aud": "authenticated",
            "iss": _ISSUER,
            "exp": 2**31,
            "email": "legacy@example.org",
        },
        secret,
        algorithm="HS256",
    )
    context = await _verifier(supabase_jwt_secret=secret).verify(token)
    assert context.email == "legacy@example.org"


async def test_unsupported_algorithm_rejected() -> None:
    # PS256 is a real algorithm but not one Supabase issues — allowlist applies.
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(
        {"sub": str(uuid4()), "aud": "authenticated", "iss": _ISSUER, "exp": 2**31},
        key,
        algorithm="PS256",
        headers={"kid": "whatever"},
    )
    with pytest.raises(AuthError, match="unsupported"):
        await _verifier().verify(token)
