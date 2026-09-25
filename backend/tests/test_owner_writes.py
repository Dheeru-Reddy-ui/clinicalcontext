"""Owner-only writes and personal settings, enforced by the database (026).

The API already asks for the owner role before it changes an organization,
sends an invite or makes an API key. These tests go around the API — raw SQL
under the context a Supabase Data API request gets (role ``authenticated``
plus the user's JWT claims) — and check that a member who is not an owner
still cannot promote themselves, raise the plan, invite, mint a key or read
a webhook's signing secret, while everything members legitimately do on
that path keeps working. Every session is rolled back.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.services.webhooks import enqueue_event

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (needs a running Postgres; see README)",
)

DENIED = asyncpg.exceptions.InsufficientPrivilegeError  # SQLSTATE 42501, grants and RLS alike


@pytest.fixture(scope="session", autouse=True)
def _migrated(migrated_database: str) -> None:
    """Migrations come from the shared session fixture in conftest."""


@asynccontextmanager
async def as_member(org_id: UUID, user_id: UUID) -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        transaction = conn.transaction()
        await transaction.start()
        claims: dict[str, Any] = {
            "role": "authenticated",
            "org_id": str(org_id),
            "sub": str(user_id),
        }
        await conn.execute("SET LOCAL ROLE authenticated")
        await conn.fetchval("SELECT set_config('request.jwt.claims', $1, true)", json.dumps(claims))
        try:
            yield conn
        finally:
            await transaction.rollback()
    finally:
        await conn.close()


@dataclass
class Org:
    id: UUID
    owner: UUID
    viewer: UUID
    clinician: UUID
    webhook: UUID


@pytest.fixture
async def org() -> AsyncIterator[Org]:
    admin = await asyncpg.connect(TEST_DATABASE_URL)
    seeded = Org(id=uuid4(), owner=uuid4(), viewer=uuid4(), clinician=uuid4(), webhook=uuid4())
    try:
        await admin.execute(
            "INSERT INTO public.organizations (id, name, slug) VALUES ($1, 'Owner writes', $2)",
            seeded.id,
            f"ow-{seeded.id.hex[:8]}",
        )
        for user_id, role in (
            (seeded.owner, "owner"),
            (seeded.viewer, "viewer"),
            (seeded.clinician, "clinician"),
        ):
            await admin.execute(
                "INSERT INTO auth.users (id, email) VALUES ($1, $2)",
                user_id,
                f"{role}-{user_id.hex[:8]}@cc-tests.org",
            )
            await admin.execute(
                "INSERT INTO public.profiles (id, org_id, role, full_name) VALUES ($1, $2, $3, $4)",
                user_id,
                seeded.id,
                role,
                role.title(),
            )
        await admin.execute(
            "INSERT INTO public.org_invites (org_id, email, role, token, expires_at) "
            "VALUES ($1, 'someone@cc-tests.org', 'owner', $2, now() + interval '1 day')",
            seeded.id,
            f"tok-{uuid4().hex}",
        )
        await admin.execute(
            "INSERT INTO public.webhooks (id, org_id, url, secret, events, created_by) "
            "VALUES ($1, $2, 'https://hooks.example.org/cc', 'whsec_test', "
            "ARRAY['query.completed'], $3)",
            seeded.webhook,
            seeded.id,
            seeded.owner,
        )
        yield seeded
    finally:
        await admin.execute("DELETE FROM public.organizations WHERE id = $1", seeded.id)
        await admin.execute(
            "DELETE FROM auth.users WHERE id = ANY($1::uuid[])",
            [seeded.owner, seeded.viewer, seeded.clinician],
        )
        await admin.close()


# -- profiles ---------------------------------------------------------------------------


async def test_a_member_cannot_make_themselves_an_owner(org: Org) -> None:
    async with as_member(org.id, org.viewer) as conn:
        with pytest.raises(DENIED):
            await conn.execute(
                "UPDATE public.profiles SET role = 'owner' WHERE id = $1", org.viewer
            )


async def test_a_member_changes_their_own_name_and_specialty_only(org: Org) -> None:
    async with as_member(org.id, org.viewer) as conn:
        own = await conn.execute(
            "UPDATE public.profiles SET full_name = 'Dr V', specialty = 'Cardiology' WHERE id = $1",
            org.viewer,
        )
        assert own == "UPDATE 1"
        other = await conn.execute(
            "UPDATE public.profiles SET full_name = 'Renamed' WHERE id = $1", org.owner
        )
        assert other == "UPDATE 0"


# -- organizations -------------------------------------------------------------------------


async def test_nobody_raises_the_plan_from_a_client_not_even_an_owner(org: Org) -> None:
    for user in (org.viewer, org.owner):
        async with as_member(org.id, user) as conn:
            with pytest.raises(DENIED):
                await conn.execute(
                    "UPDATE public.organizations SET plan = 'enterprise' WHERE id = $1", org.id
                )


async def test_only_an_owner_changes_organization_settings(org: Org) -> None:
    async with as_member(org.id, org.clinician) as conn:
        changed = await conn.execute(
            "UPDATE public.organizations SET public_sharing_enabled = false WHERE id = $1", org.id
        )
        assert changed == "UPDATE 0"
    async with as_member(org.id, org.owner) as conn:
        changed = await conn.execute(
            "UPDATE public.organizations SET public_sharing_enabled = false, "
            "voice_tts_quality = 'multilingual' WHERE id = $1",
            org.id,
        )
        assert changed == "UPDATE 1"


# -- invites and API keys --------------------------------------------------------------------


async def test_a_member_can_neither_see_nor_send_invites(org: Org) -> None:
    async with as_member(org.id, org.clinician) as conn:
        assert await conn.fetchval("SELECT count(*) FROM public.org_invites") == 0
        with pytest.raises(DENIED):
            await conn.execute(
                "INSERT INTO public.org_invites (org_id, email, role, token, expires_at) "
                "VALUES ($1, 'mine@cc-tests.org', 'owner', $2, now() + interval '1 day')",
                org.id,
                f"tok-{uuid4().hex}",
            )
    async with as_member(org.id, org.owner) as conn:
        assert await conn.fetchval("SELECT count(*) FROM public.org_invites") == 1
        await conn.execute(
            "INSERT INTO public.org_invites (org_id, email, role, token, expires_at) "
            "VALUES ($1, 'colleague@cc-tests.org', 'clinician', $2, now() + interval '1 day')",
            org.id,
            f"tok-{uuid4().hex}",
        )


async def test_a_member_cannot_mint_an_api_key(org: Org) -> None:
    key_row = (org.id, "sneaky", f"hash-{uuid4().hex}", "cck_sneak", ["full"])
    async with as_member(org.id, org.viewer) as conn:
        with pytest.raises(DENIED):
            await conn.execute(
                "INSERT INTO public.api_keys "
                "(org_id, name, key_hash, key_prefix, scopes, created_by) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                *key_row,
                org.viewer,
            )
    async with as_member(org.id, org.owner) as conn:
        await conn.execute(
            "INSERT INTO public.api_keys "
            "(org_id, name, key_hash, key_prefix, scopes, created_by) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            *key_row,
            org.owner,
        )
        assert await conn.fetchval("SELECT count(*) FROM public.api_keys") == 1


# -- webhooks -------------------------------------------------------------------------------------


async def test_members_see_webhooks_but_not_their_secret_and_cannot_add_one(org: Org) -> None:
    async with as_member(org.id, org.clinician) as conn:
        assert await conn.fetchval("SELECT count(*) FROM public.webhooks") == 1
        with pytest.raises(DENIED):
            await conn.fetchval("SELECT secret FROM public.webhooks")
    async with as_member(org.id, org.clinician) as conn:
        with pytest.raises(DENIED):
            await conn.execute(
                "INSERT INTO public.webhooks (org_id, url, secret, events) "
                "VALUES ($1, 'https://evil.example.org/x', 's', ARRAY['query.completed'])",
                org.id,
            )


async def test_a_members_own_work_still_queues_its_webhook_deliveries(org: Org) -> None:
    # The request path queues deliveries inside the member's transaction (017):
    # that needs to see the organization's webhooks, secret or not.
    async with as_member(org.id, org.clinician) as conn:
        queued = await enqueue_event(
            conn, org_id=org.id, event="query.completed", payload={"query_id": "q"}
        )
        assert queued == 1


# -- personal settings -----------------------------------------------------------------------------


async def test_preferences_are_private_to_their_owner(org: Org) -> None:
    async with as_member(org.id, org.viewer) as conn:
        await conn.execute(
            "INSERT INTO public.user_preferences (user_id, org_id, audience) "
            "VALUES ($1, $2, 'student')",
            org.viewer,
            org.id,
        )
        with pytest.raises(DENIED):
            await conn.execute(
                "INSERT INTO public.user_preferences (user_id, org_id) VALUES ($1, $2)",
                org.owner,
                org.id,
            )
    admin = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await admin.execute(
            "INSERT INTO public.user_preferences (user_id, org_id, audience) "
            "VALUES ($1, $2, 'clinician')",
            org.owner,
            org.id,
        )
        async with as_member(org.id, org.viewer) as conn:
            assert await conn.fetchval("SELECT count(*) FROM public.user_preferences") == 0
    finally:
        await admin.execute("DELETE FROM public.user_preferences WHERE user_id = $1", org.owner)
        await admin.close()
