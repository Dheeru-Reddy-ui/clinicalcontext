"""End-to-end auth + RBAC against the real API and a real migrated database.

Tokens are genuine RS256 JWTs verified through the production code path; the
only substitution is the injected JWKS keypair. Users are seeded in
auth.users exactly as GoTrue would have created them.
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.core.security import JWKSCache, JWTVerifier
from app.main import create_app
from tests.conftest import mint_token, test_jwks

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (needs a running Postgres; see README)",
)


@dataclass
class RbacEnv:
    app: FastAPI
    client: AsyncClient
    admin: asyncpg.Connection
    org_ids: list[UUID] = field(default_factory=list)
    user_ids: list[UUID] = field(default_factory=list)

    async def new_user(
        self, email: str | None = None, full_name: str | None = None
    ) -> tuple[UUID, str]:
        """Seed auth.users (as GoTrue would) and mint a matching token."""
        user_id = uuid4()
        resolved_email = email or f"user-{user_id.hex[:8]}@cc-tests.org"
        await self.admin.execute(
            "INSERT INTO auth.users (id, email) VALUES ($1, $2)", user_id, resolved_email
        )
        self.user_ids.append(user_id)
        return user_id, mint_token(sub=user_id, email=resolved_email, full_name=full_name)

    @staticmethod
    def auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def track_org(self, org_id: str | UUID) -> None:
        self.org_ids.append(UUID(str(org_id)))


@pytest.fixture
async def env(migrated_database: str) -> AsyncIterator[RbacEnv]:
    app = create_app()
    app.state.jwt_verifier = JWTVerifier(
        get_settings(), jwks=JWKSCache("unused://in-tests", preloaded=test_jwks())
    )
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=3)
    app.state.db_pool = pool
    admin = await asyncpg.connect(TEST_DATABASE_URL)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        environment = RbacEnv(app=app, client=client, admin=admin)
        try:
            yield environment
        finally:
            if environment.org_ids:
                await admin.execute("SET session_replication_role = 'replica'")
                await admin.execute(
                    "DELETE FROM public.audit_log WHERE org_id = ANY($1)",
                    environment.org_ids,
                )
                await admin.execute("RESET session_replication_role")
                await admin.execute(
                    "DELETE FROM public.organizations WHERE id = ANY($1)",
                    environment.org_ids,
                )
            if environment.user_ids:
                await admin.execute(
                    "DELETE FROM auth.users WHERE id = ANY($1)", environment.user_ids
                )
    await admin.close()
    await pool.close()


async def _bootstrap_org(env: RbacEnv, *, org_name: str) -> tuple[UUID, str, dict]:
    """Create a fresh owner + org; returns (user_id, token, me-payload)."""
    user_id, token = await env.new_user(full_name="Owner User")
    response = await env.client.post(
        "/api/v1/auth/bootstrap", json={"org_name": org_name}, headers=env.auth(token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    env.track_org(body["org"]["id"])
    return user_id, token, body


async def _join_via_invite(env: RbacEnv, owner_token: str, *, role: str) -> tuple[UUID, str, dict]:
    """Owner invites a new user who accepts; returns (user_id, token, me)."""
    member_id, member_token = await env.new_user()
    email = await env.admin.fetchval("SELECT email FROM auth.users WHERE id = $1", member_id)
    invite = await env.client.post(
        "/api/v1/orgs/invites",
        json={"email": email, "role": role},
        headers=env.auth(owner_token),
    )
    assert invite.status_code == 201, invite.text
    accepted = await env.client.post(
        "/api/v1/orgs/invites/accept",
        json={"token": invite.json()["token"]},
        headers=env.auth(member_token),
    )
    assert accepted.status_code == 200, accepted.text
    return member_id, member_token, accepted.json()


# -- the unauthenticated sweep (spec item 7) -----------------------------------------


async def test_every_v1_route_rejects_unauthenticated_requests(env: RbacEnv) -> None:
    """Every operation under /api/v1 must 401 without a token — enumerated
    from the OpenAPI schema so new routes are swept automatically."""
    schema = env.app.openapi()
    checked = 0
    for path, operations in schema["paths"].items():
        if not path.startswith("/api/v1"):
            continue
        concrete_path = re.sub(r"\{[^}]+\}", str(uuid4()), path)
        for method in operations:
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            response = await env.client.request(method.upper(), concrete_path, json={})
            assert response.status_code == 401, (
                f"{method.upper()} {concrete_path} returned {response.status_code}, expected 401"
            )
            assert response.json()["error"]["code"] == "auth_error"
            checked += 1
    assert checked >= 8, f"sweep covered only {checked} operations"


# -- bootstrap ------------------------------------------------------------------------


async def test_bootstrap_creates_org_and_owner_profile(env: RbacEnv) -> None:
    user_id, _token, me = await _bootstrap_org(env, org_name="Cardiology Group")

    assert me["role"] == "owner"
    assert me["org"]["name"] == "Cardiology Group"
    assert me["org"]["slug"].startswith("cardiology-group")

    profile = await env.admin.fetchrow(
        "SELECT org_id, role FROM public.profiles WHERE id = $1", user_id
    )
    assert profile is not None and profile["role"] == "owner"
    audit = await env.admin.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE org_id = $1 AND action = 'org.created'",
        UUID(me["org"]["id"]),
    )
    assert audit == 1


async def test_bootstrap_is_idempotent(env: RbacEnv) -> None:
    _, token, first = await _bootstrap_org(env, org_name="Once Only")
    again = await env.client.post(
        "/api/v1/auth/bootstrap", json={"org_name": "Ignored"}, headers=env.auth(token)
    )
    assert again.status_code == 200
    assert again.json()["org"]["id"] == first["org"]["id"]


async def test_me_requires_bootstrap(env: RbacEnv) -> None:
    _, token = await env.new_user()
    response = await env.client.get("/api/v1/orgs/me", headers=env.auth(token))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "org_membership_required"


async def test_two_orgs_can_share_a_name(env: RbacEnv) -> None:
    """The slug is derived from the name, so the second organisation to pick a
    name collides on it. The retry has to run in a savepoint: a failed INSERT
    aborts the whole transaction in Postgres, so retrying without one used to
    fail the request with 500 (found by the Phase 14 E2E suite)."""
    _, _, first = await _bootstrap_org(env, org_name="Riverside Cardiology")
    _, _, second = await _bootstrap_org(env, org_name="Riverside Cardiology")

    assert first["org"]["id"] != second["org"]["id"]
    assert first["org"]["name"] == second["org"]["name"] == "Riverside Cardiology"
    assert first["org"]["slug"] != second["org"]["slug"]
    assert second["org"]["slug"].startswith("riverside-cardiology")


async def test_create_org_conflicts_when_already_member(env: RbacEnv) -> None:
    _, token, _ = await _bootstrap_org(env, org_name="First Org")
    response = await env.client.post(
        "/api/v1/orgs", json={"name": "Second Org"}, headers=env.auth(token)
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_in_organization"


# -- invites --------------------------------------------------------------------------


async def test_invite_flow_lands_clinician_in_same_org(env: RbacEnv) -> None:
    _, owner_token, owner_me = await _bootstrap_org(env, org_name="Invite Org")
    _, _, member_me = await _join_via_invite(env, owner_token, role="clinician")

    assert member_me["org"]["id"] == owner_me["org"]["id"]
    assert member_me["role"] == "clinician"

    members = await env.client.get("/api/v1/orgs/members", headers=env.auth(owner_token))
    assert members.status_code == 200
    listed = members.json()["members"]
    assert len(listed) == 2
    assert {m["role"] for m in listed} == {"owner", "clinician"}
    assert all(m["email"] for m in listed)

    audit = await env.admin.fetchval(
        "SELECT count(*) FROM public.audit_log "
        "WHERE org_id = $1 AND action = 'org.invite.accepted'",
        UUID(owner_me["org"]["id"]),
    )
    assert audit == 1


async def test_bootstrap_with_invite_token_joins_org(env: RbacEnv) -> None:
    _, owner_token, owner_me = await _bootstrap_org(env, org_name="Bootstrap Join")
    joiner_id, joiner_token = await env.new_user(email=f"joiner-{uuid4().hex[:6]}@cc-tests.org")
    email = await env.admin.fetchval("SELECT email FROM auth.users WHERE id = $1", joiner_id)
    invite = await env.client.post(
        "/api/v1/orgs/invites",
        json={"email": email, "role": "viewer"},
        headers=env.auth(owner_token),
    )
    response = await env.client.post(
        "/api/v1/auth/bootstrap",
        json={"invite_token": invite.json()["token"]},
        headers=env.auth(joiner_token),
    )
    assert response.status_code == 200
    assert response.json()["org"]["id"] == owner_me["org"]["id"]
    assert response.json()["role"] == "viewer"


async def test_invite_for_someone_else_cannot_be_used(env: RbacEnv) -> None:
    _, owner_token, _ = await _bootstrap_org(env, org_name="Mismatch Org")
    invite = await env.client.post(
        "/api/v1/orgs/invites",
        json={"email": "intended@cc-tests.org", "role": "clinician"},
        headers=env.auth(owner_token),
    )
    _, interloper_token = await env.new_user(email=f"interloper-{uuid4().hex[:6]}@cc-tests.org")
    response = await env.client.post(
        "/api/v1/orgs/invites/accept",
        json={"token": invite.json()["token"]},
        headers=env.auth(interloper_token),
    )
    assert response.status_code == 404  # indistinguishable from an unknown token


async def test_expired_invite_cannot_be_used(env: RbacEnv) -> None:
    _, owner_token, _ = await _bootstrap_org(env, org_name="Expired Org")
    member_id, member_token = await env.new_user(email=f"late-{uuid4().hex[:6]}@cc-tests.org")
    email = await env.admin.fetchval("SELECT email FROM auth.users WHERE id = $1", member_id)
    invite = await env.client.post(
        "/api/v1/orgs/invites",
        json={"email": email, "role": "clinician"},
        headers=env.auth(owner_token),
    )
    await env.admin.execute(
        "UPDATE public.org_invites SET expires_at = now() - interval '1 hour' WHERE id = $1",
        UUID(invite.json()["id"]),
    )
    response = await env.client.post(
        "/api/v1/orgs/invites/accept",
        json={"token": invite.json()["token"]},
        headers=env.auth(member_token),
    )
    assert response.status_code == 404


# -- RBAC (spec item 7) -----------------------------------------------------------------


async def test_clinician_cannot_invite(env: RbacEnv) -> None:
    _, owner_token, _ = await _bootstrap_org(env, org_name="RBAC Org A")
    _, clinician_token, _ = await _join_via_invite(env, owner_token, role="clinician")

    response = await env.client.post(
        "/api/v1/orgs/invites",
        json={"email": "someone@cc-tests.org", "role": "viewer"},
        headers=env.auth(clinician_token),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


async def test_clinician_cannot_change_roles(env: RbacEnv) -> None:
    owner_id, owner_token, _ = await _bootstrap_org(env, org_name="RBAC Org B")
    _, clinician_token, _ = await _join_via_invite(env, owner_token, role="clinician")

    response = await env.client.patch(
        f"/api/v1/orgs/members/{owner_id}/role",
        json={"role": "viewer"},
        headers=env.auth(clinician_token),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


async def test_viewer_cannot_invite(env: RbacEnv) -> None:
    _, owner_token, _ = await _bootstrap_org(env, org_name="RBAC Org C")
    _, viewer_token, _ = await _join_via_invite(env, owner_token, role="viewer")

    response = await env.client.post(
        "/api/v1/orgs/invites",
        json={"email": "x@cc-tests.org", "role": "viewer"},
        headers=env.auth(viewer_token),
    )
    assert response.status_code == 403


async def test_owner_can_change_roles_and_it_takes_effect_immediately(env: RbacEnv) -> None:
    _, owner_token, _ = await _bootstrap_org(env, org_name="Promotion Org")
    member_id, member_token, _ = await _join_via_invite(env, owner_token, role="clinician")

    promoted = await env.client.patch(
        f"/api/v1/orgs/members/{member_id}/role",
        json={"role": "owner"},
        headers=env.auth(owner_token),
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "owner"

    # The promoted member can now invite â€” role read fresh from the DB.
    response = await env.client.post(
        "/api/v1/orgs/invites",
        json={"email": "new@cc-tests.org", "role": "clinician"},
        headers=env.auth(member_token),
    )
    assert response.status_code == 201


async def test_last_owner_cannot_be_demoted(env: RbacEnv) -> None:
    owner_id, owner_token, _ = await _bootstrap_org(env, org_name="Lonely Owner Org")
    response = await env.client.patch(
        f"/api/v1/orgs/members/{owner_id}/role",
        json={"role": "clinician"},
        headers=env.auth(owner_token),
    )
    assert response.status_code == 403
    assert "last owner" in response.json()["error"]["message"]


async def test_role_change_scoped_to_own_org(env: RbacEnv) -> None:
    """An owner of org X cannot touch a member of org Y â€” 404, not 403:
    the foreign profile is simply invisible."""
    _, owner_a_token, _ = await _bootstrap_org(env, org_name="Org X")
    member_b_id, _, _ = await _bootstrap_org(env, org_name="Org Y")

    response = await env.client.patch(
        f"/api/v1/orgs/members/{member_b_id}/role",
        json={"role": "viewer"},
        headers=env.auth(owner_a_token),
    )
    assert response.status_code == 404
