"""Test bootstrap.

Provides a complete environment *before* any app module resolves settings, so
the suite runs identically on a bare CI runner and a developer machine. Real
env vars (or a local .env) still win via ``setdefault`` — tests never talk to
real services, so the values only need to parse.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator

from pydantic import ValidationError

from app.config import Settings

# The integration suites (tests/test_rls.py, test_ask_flow.py, test_phase9_api.py)
# run against a real migrated Postgres and a real Redis. Resolve their URLs from
# the developer's .env BEFORE the synthetic unit-test defaults below are injected
# into os.environ (env vars outrank .env files, so doing this later would hand the
# suites the bogus URLs — and a dead Redis makes them hang, not fail). CI sets
# TEST_DATABASE_URL / TEST_REDIS_URL explicitly; without either, those suites skip.
if "TEST_DATABASE_URL" not in os.environ or "TEST_REDIS_URL" not in os.environ:
    with contextlib.suppress(ValidationError):
        _real = Settings()
        os.environ.setdefault("TEST_DATABASE_URL", _real.database_url)
        os.environ.setdefault("TEST_REDIS_URL", _real.redis_url)

_TEST_ENV = {
    "ENVIRONMENT": "local",
    "LOG_LEVEL": "WARNING",
    "CORS_ORIGINS": "http://localhost:3000",
    "DATABASE_URL": "postgresql://test:test@127.0.0.1:59999/test",
    "REDIS_URL": "redis://127.0.0.1:59998/0",
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_ANON_KEY": "test-anon-key",
    "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-key",
    "COHERE_API_KEY": "test-cohere-key",
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "LANGSMITH_API_KEY": "test-langsmith-key",
    "DEEPGRAM_API_KEY": "test-deepgram-key",
    "ELEVENLABS_API_KEY": "test-elevenlabs-key",
}

for _key, _value in _TEST_ENV.items():
    os.environ.setdefault(_key, _value)

import asyncio  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Any  # noqa: E402
from uuid import UUID, uuid4  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.services.semantic_cache import clear_semantic_cache  # noqa: E402


@pytest.fixture
def test_app() -> FastAPI:
    """A fresh app instance (no lifespan run — app.state starts empty)."""
    return create_app()


@pytest.fixture
async def client(test_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


# -- database (integration suites) ---------------------------------------------------


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Apply the local shim + all migrations once per session (idempotent).

    Returns the database URL. Suites that need it must also skip when
    TEST_DATABASE_URL is unset.
    """
    url = os.environ.get("TEST_DATABASE_URL", "")
    if url:
        from scripts.migrate import apply_migrations

        asyncio.run(apply_migrations(url, include_local_shim=True))
    return url


# -- the live API harness (real app + real Postgres + real Redis) ---------------------
# Shared by the Phase 9 suites. Tokens are genuine RS256 JWTs verified through
# the production code path; only the JWKS keypair is injected.


def parse_sse(body: str) -> list[dict[str, Any]]:
    """Parse a text/event-stream body into its JSON event payloads."""
    events: list[dict[str, Any]] = []
    for block in body.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


@dataclass
class ApiEnv:
    app: FastAPI
    client: AsyncClient
    pool: Any
    redis: Any
    admin: Any
    org_ids: list[UUID] = field(default_factory=list)
    user_ids: list[UUID] = field(default_factory=list)

    async def new_org_with_owner(self, *, plan: str = "free") -> tuple[UUID, UUID, str]:
        """Seed an org (on the given plan) plus an owner profile and token."""
        org_id = await self.admin.fetchval(
            "INSERT INTO public.organizations (name, slug, plan) VALUES ($1, $2, $3) RETURNING id",
            "Phase9",
            f"p9-{uuid4().hex[:8]}",
            plan,
        )
        self.org_ids.append(org_id)
        user_id, token = await self.add_member(org_id, "owner")
        return org_id, user_id, token

    async def add_member(self, org_id: UUID, role: str) -> tuple[UUID, str]:
        user_id = uuid4()
        email = f"{role}-{user_id.hex[:8]}@cc-tests.org"
        await self.admin.execute(
            "INSERT INTO auth.users (id, email) VALUES ($1, $2)", user_id, email
        )
        self.user_ids.append(user_id)
        await self.admin.execute(
            "INSERT INTO public.profiles (id, org_id, role) VALUES ($1, $2, $3)",
            user_id,
            org_id,
            role,
        )
        return user_id, mint_token(sub=user_id, email=email, org_id=org_id)

    @staticmethod
    def auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def env(migrated_database: str) -> AsyncIterator[ApiEnv]:
    import asyncpg
    from redis.asyncio import Redis

    from app.config import get_settings
    from app.core.security import JWKSCache, JWTVerifier

    database_url = os.environ.get("TEST_DATABASE_URL", "")
    redis_url = os.environ.get("TEST_REDIS_URL", "")
    if not (database_url and redis_url):  # pragma: no cover — suites skip first
        pytest.skip("TEST_DATABASE_URL/TEST_REDIS_URL not set")

    app = create_app()
    app.state.jwt_verifier = JWTVerifier(
        get_settings(), jwks=JWKSCache("unused://in-tests", preloaded=test_jwks())
    )
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=4)
    # Explicit timeouts (as the app's lifespan uses): an unreachable Redis must
    # fail the suite fast, never hang it.
    redis = Redis.from_url(redis_url, socket_connect_timeout=5, socket_timeout=5)
    app.state.db_pool = pool
    app.state.redis = redis
    admin = await asyncpg.connect(database_url)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        environment = ApiEnv(app=app, client=client, pool=pool, redis=redis, admin=admin)
        try:
            yield environment
        finally:
            for org_id in environment.org_ids:
                await clear_semantic_cache(redis, org_id)
                await redis.delete(f"rl:t:{org_id}")
            for user_id in environment.user_ids:
                await redis.delete(f"rl:u:{user_id}")
            if environment.org_ids:
                # audit_log and answer_versions are append-only by trigger, and
                # answer_versions RESTRICTs deleting the org — that is the schema
                # protecting clinical history, working as intended. Test data
                # still has to go, so clear them with replication triggers off.
                await admin.execute("SET session_replication_role = 'replica'")
                await admin.execute(
                    "DELETE FROM public.audit_log WHERE org_id = ANY($1)", environment.org_ids
                )
                await admin.execute(
                    "DELETE FROM public.answer_versions WHERE org_id = ANY($1)",
                    environment.org_ids,
                )
                await admin.execute("RESET session_replication_role")
                await admin.execute(
                    "DELETE FROM public.organizations WHERE id = ANY($1)", environment.org_ids
                )
            if environment.user_ids:
                await admin.execute(
                    "DELETE FROM auth.users WHERE id = ANY($1)", environment.user_ids
                )
    await admin.close()
    await pool.close()
    await redis.aclose()


# -- JWT test infrastructure -----------------------------------------------------------
# A real RSA keypair minting production-shaped Supabase tokens, verified by the
# app through the exact same JWKS code path as production — only the key
# material is injected instead of fetched.

TEST_JWT_KID = "test-key-1"
_test_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_jwks() -> dict[str, Any]:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(_test_private_key.public_key(), as_dict=True)
    jwk.update({"kid": TEST_JWT_KID, "alg": "RS256", "use": "sig"})
    return {"keys": [jwk]}


def mint_token(
    *,
    sub: UUID | str,
    email: str | None = None,
    full_name: str | None = None,
    org_id: UUID | str | None = None,
    audience: str = "authenticated",
    issuer: str = "https://example.supabase.co/auth/v1",
    expires_in: int = 3600,
    kid: str = TEST_JWT_KID,
) -> str:
    """Mint a Supabase-shaped access token signed with the test key."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": str(sub),
        "aud": audience,
        "iss": issuer,
        "iat": now,
        "exp": now + expires_in,
        "role": "authenticated",
    }
    if email is not None:
        claims["email"] = email
    if full_name is not None:
        claims["user_metadata"] = {"full_name": full_name}
    if org_id is not None:
        claims["app_metadata"] = {"org_id": str(org_id)}
    return jwt.encode(claims, _test_private_key, algorithm="RS256", headers={"kid": kid})
