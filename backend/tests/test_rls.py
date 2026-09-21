"""Tenant isolation proven at the database layer.

These tests speak raw SQL to a real migrated Postgres under the exact context
a Supabase request gets (role ``authenticated`` + ``request.jwt.claims``) and
try to break out of the tenant: cross-tenant reads, writes, deletes, moving
rows between orgs, writing the shared corpus, and mutating append-only
tables. Every attempt must fail *at the database*, not in application code.

Requires TEST_DATABASE_URL (resolved automatically from .env locally — start
``docker compose up -d`` first; provided explicitly in CI). Migrations and the
local auth shim are applied automatically and idempotently.
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

from app.repositories.base import TenantScopedRepository

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (needs a running Postgres; see README)",
)


# -- environment -----------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _migrated(migrated_database: str) -> None:
    """Migrations come from the shared session fixture in conftest."""


@pytest.fixture
async def admin() -> AsyncIterator[asyncpg.Connection]:
    """Superuser connection: seeds fixtures and stands in for the service role."""
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        yield conn
    finally:
        await conn.close()


@asynccontextmanager
async def tenant_session(
    org_id: UUID | None,
    user_id: UUID | None = None,
) -> AsyncIterator[asyncpg.Connection]:
    """A connection scoped exactly like a Supabase request for this tenant.

    Everything runs in one transaction that is always rolled back — assertions
    happen inside; the database stays clean.
    """
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        transaction = conn.transaction()
        await transaction.start()
        claims: dict[str, Any] = {"role": "authenticated"}
        if org_id is not None:
            claims["org_id"] = str(org_id)
        if user_id is not None:
            claims["sub"] = str(user_id)
        await conn.execute("SET LOCAL ROLE authenticated")
        await conn.fetchval("SELECT set_config('request.jwt.claims', $1, true)", json.dumps(claims))
        try:
            yield conn
        finally:
            await transaction.rollback()
    finally:
        await conn.close()


@dataclass
class Seed:
    org_a: UUID
    org_b: UUID
    user_a: UUID
    user_b: UUID
    doc_a: UUID
    doc_b: UUID
    doc_shared: UUID
    chunk_a: UUID
    chunk_shared: UUID
    answer_a: UUID
    version_a: UUID


@pytest.fixture
async def seed(admin: asyncpg.Connection) -> AsyncIterator[Seed]:
    """Two orgs, two users, private + shared corpus rows, one answer with history."""
    suffix = uuid4().hex[:8]
    ids = Seed(
        org_a=uuid4(),
        org_b=uuid4(),
        user_a=uuid4(),
        user_b=uuid4(),
        doc_a=uuid4(),
        doc_b=uuid4(),
        doc_shared=uuid4(),
        chunk_a=uuid4(),
        chunk_shared=uuid4(),
        answer_a=uuid4(),
        version_a=uuid4(),
    )
    session_a, query_a = uuid4(), uuid4()

    await admin.execute(
        "INSERT INTO public.organizations (id, name, slug) VALUES "
        "($1, 'Org A', $3), ($2, 'Org B', $4)",
        ids.org_a,
        ids.org_b,
        f"org-a-{suffix}",
        f"org-b-{suffix}",
    )
    await admin.execute(
        "INSERT INTO auth.users (id, email) VALUES ($1, $3), ($2, $4)",
        ids.user_a,
        ids.user_b,
        f"a-{suffix}@test.local",
        f"b-{suffix}@test.local",
    )
    await admin.execute(
        "INSERT INTO public.profiles (id, org_id, role) VALUES "
        "($1, $3, 'owner'), ($2, $4, 'owner')",
        ids.user_a,
        ids.user_b,
        ids.org_a,
        ids.org_b,
    )
    await admin.execute(
        """
        INSERT INTO public.documents (id, org_id, source_type, title, content_hash) VALUES
          ($1, $4, 'uploaded', 'Org A private protocol',  $6),
          ($2, $5, 'uploaded', 'Org B private protocol',  $7),
          ($3, NULL, 'pubmed', 'Shared corpus RCT',       $8)
        """,
        ids.doc_a,
        ids.doc_b,
        ids.doc_shared,
        ids.org_a,
        ids.org_b,
        f"hash-a-{suffix}",
        f"hash-b-{suffix}",
        f"hash-shared-{suffix}",
    )
    await admin.execute(
        """
        INSERT INTO public.chunks (id, document_id, org_id, chunk_index, content, token_count)
        VALUES ($1, $3, $5, 0, 'private passage of org A', 10),
               ($2, $4, NULL, 0, 'shared corpus passage', 10)
        """,
        ids.chunk_a,
        ids.chunk_shared,
        ids.doc_a,
        ids.doc_shared,
        ids.org_a,
    )
    await admin.execute(
        "INSERT INTO public.query_sessions (id, org_id, user_id) VALUES ($1, $2, $3)",
        session_a,
        ids.org_a,
        ids.user_a,
    )
    await admin.execute(
        "INSERT INTO public.queries (id, session_id, org_id, user_id, raw_query, status) "
        "VALUES ($1, $2, $3, $4, 'apixaban in ckd4?', 'completed')",
        query_a,
        session_a,
        ids.org_a,
        ids.user_a,
    )
    await admin.execute(
        "INSERT INTO public.answers (id, query_id, org_id, content, model, prompt_version) "
        "VALUES ($1, $2, $3, 'answer v1 content', 'test-model', 'test.v1')",
        ids.answer_a,
        query_a,
        ids.org_a,
    )
    await admin.execute(
        "INSERT INTO public.answer_versions (id, answer_id, org_id, version, content) "
        "VALUES ($1, $2, $3, 1, 'answer v1 content')",
        ids.version_a,
        ids.answer_a,
        ids.org_a,
    )

    yield ids

    # Teardown. Append-only tables refuse DELETE by trigger; superuser
    # replica mode is the documented break-glass path (skips triggers).
    await admin.execute("SET session_replication_role = 'replica'")
    await admin.execute(
        "DELETE FROM public.answer_versions WHERE org_id IN ($1, $2)", ids.org_a, ids.org_b
    )
    await admin.execute(
        "DELETE FROM public.audit_log WHERE org_id IN ($1, $2)", ids.org_a, ids.org_b
    )
    await admin.execute("RESET session_replication_role")
    await admin.execute(
        "DELETE FROM public.organizations WHERE id IN ($1, $2)", ids.org_a, ids.org_b
    )
    await admin.execute("DELETE FROM public.documents WHERE id = $1", ids.doc_shared)
    await admin.execute("DELETE FROM auth.users WHERE id IN ($1, $2)", ids.user_a, ids.user_b)


# -- the invariant everything else rests on ---------------------------------------


async def test_every_public_table_has_rls_enabled(admin: asyncpg.Connection) -> None:
    unprotected = await admin.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND NOT rowsecurity"
    )
    assert [r["tablename"] for r in unprotected] == []


# -- cross-tenant reads ------------------------------------------------------------


async def test_tenant_reads_own_private_document(seed: Seed) -> None:
    async with tenant_session(seed.org_a, seed.user_a) as conn:
        row = await conn.fetchrow("SELECT * FROM public.documents WHERE id = $1", seed.doc_a)
    assert row is not None
    assert row["title"] == "Org A private protocol"


async def test_cross_tenant_document_read_denied(seed: Seed) -> None:
    async with tenant_session(seed.org_b, seed.user_b) as conn:
        row = await conn.fetchrow("SELECT * FROM public.documents WHERE id = $1", seed.doc_a)
        visible_in_a = await conn.fetchval(
            "SELECT count(*) FROM public.documents WHERE org_id = $1", seed.org_a
        )
    assert row is None
    assert visible_in_a == 0


async def test_shared_corpus_readable_by_both_tenants(seed: Seed) -> None:
    for org, user in ((seed.org_a, seed.user_a), (seed.org_b, seed.user_b)):
        async with tenant_session(org, user) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM public.documents WHERE id = $1", seed.doc_shared
            )
        assert row is not None, f"shared corpus must be visible to org {org}"


async def test_chunks_follow_document_visibility(seed: Seed) -> None:
    async with tenant_session(seed.org_b, seed.user_b) as conn:
        private = await conn.fetchrow("SELECT * FROM public.chunks WHERE id = $1", seed.chunk_a)
        shared = await conn.fetchrow("SELECT * FROM public.chunks WHERE id = $1", seed.chunk_shared)
    assert private is None
    assert shared is not None


async def test_profiles_visible_within_org_only(seed: Seed) -> None:
    async with tenant_session(seed.org_b, seed.user_b) as conn:
        foreign = await conn.fetchrow("SELECT * FROM public.profiles WHERE id = $1", seed.user_a)
        own = await conn.fetchrow("SELECT * FROM public.profiles WHERE id = $1", seed.user_b)
    assert foreign is None
    assert own is not None


async def test_jwt_without_org_claim_sees_no_tenant_rows(seed: Seed) -> None:
    """Fail closed: a token with no org claim gets the public corpus and nothing else."""
    async with tenant_session(org_id=None) as conn:
        tenant_docs = await conn.fetchval(
            "SELECT count(*) FROM public.documents WHERE org_id IS NOT NULL"
        )
        shared = await conn.fetchrow(
            "SELECT * FROM public.documents WHERE id = $1", seed.doc_shared
        )
    assert tenant_docs == 0
    assert shared is not None


# -- cross-tenant writes -----------------------------------------------------------


async def test_cross_tenant_update_denied(seed: Seed) -> None:
    async with tenant_session(seed.org_b, seed.user_b) as conn:
        status = await conn.execute(
            "UPDATE public.documents SET title = 'hijacked' WHERE id = $1", seed.doc_a
        )
    assert status == "UPDATE 0"
    async with tenant_session(seed.org_a, seed.user_a) as conn:
        title = await conn.fetchval("SELECT title FROM public.documents WHERE id = $1", seed.doc_a)
    assert title == "Org A private protocol"


async def test_cross_tenant_delete_denied(seed: Seed) -> None:
    async with tenant_session(seed.org_b, seed.user_b) as conn:
        status = await conn.execute("DELETE FROM public.documents WHERE id = $1", seed.doc_a)
    assert status == "DELETE 0"


async def test_insert_into_other_org_denied(seed: Seed) -> None:
    async with tenant_session(seed.org_b, seed.user_b) as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO public.documents (org_id, source_type, title, content_hash) "
                "VALUES ($1, 'uploaded', 'planted in org A', $2)",
                seed.org_a,
                f"hash-planted-{uuid4().hex}",
            )


async def test_document_cannot_move_across_orgs(seed: Seed) -> None:
    """UPDATE's WITH CHECK pins org_id: donating or stealing a row is impossible."""
    async with tenant_session(seed.org_a, seed.user_a) as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "UPDATE public.documents SET org_id = $1 WHERE id = $2",
                seed.org_b,
                seed.doc_a,
            )


# -- the shared corpus is read-only for tenants -------------------------------------


async def test_tenant_cannot_write_shared_corpus(seed: Seed) -> None:
    async with tenant_session(seed.org_a, seed.user_a) as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO public.documents (org_id, source_type, title, content_hash) "
                "VALUES (NULL, 'pubmed', 'fake public paper', $1)",
                f"hash-fake-{uuid4().hex}",
            )
    async with tenant_session(seed.org_a, seed.user_a) as conn:
        update = await conn.execute(
            "UPDATE public.documents SET title = 'defaced' WHERE id = $1", seed.doc_shared
        )
        delete = await conn.execute("DELETE FROM public.documents WHERE id = $1", seed.doc_shared)
    assert update == "UPDATE 0"
    assert delete == "DELETE 0"


async def test_chunk_org_is_derived_not_trusted(admin: asyncpg.Connection, seed: Seed) -> None:
    """The sync trigger overwrites whatever org_id the writer claims."""
    stored_org = await admin.fetchval(
        "INSERT INTO public.chunks (document_id, org_id, chunk_index, content, token_count) "
        "VALUES ($1, $2, 99, 'lying about my org', 5) RETURNING org_id",
        seed.doc_a,
        seed.org_b,  # claims org B, parent document is org A
    )
    assert stored_org == seed.org_a


# -- append-only tables --------------------------------------------------------------


async def test_audit_log_is_insert_only_for_tenants(seed: Seed) -> None:
    async with tenant_session(seed.org_a, seed.user_a) as conn:
        inserted = await conn.fetchrow(
            "INSERT INTO public.audit_log (org_id, user_id, action) "
            "VALUES ($1, $2, 'test.action') RETURNING id",
            seed.org_a,
            seed.user_a,
        )
        assert inserted is not None
    # UPDATE/DELETE are denied at the privilege level (REVOKE in 006) before
    # RLS is even consulted — a hard error, not a silent zero-row match.
    # Each attempt gets its own session: the raise aborts the transaction.
    async with tenant_session(seed.org_a, seed.user_a) as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "UPDATE public.audit_log SET action = 'tampered' WHERE org_id = $1",
                seed.org_a,
            )
    async with tenant_session(seed.org_a, seed.user_a) as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute("DELETE FROM public.audit_log WHERE org_id = $1", seed.org_a)


async def test_audit_log_immutable_even_for_service_paths(
    admin: asyncpg.Connection, seed: Seed
) -> None:
    """The 004 trigger stops UPDATE/DELETE regardless of role — service_role
    and table owner included."""
    audit_id = await admin.fetchval(
        "INSERT INTO public.audit_log (org_id, action) VALUES ($1, 'svc.action') RETURNING id",
        seed.org_a,
    )
    with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
        await admin.execute(
            "UPDATE public.audit_log SET action = 'rewritten' WHERE id = $1", audit_id
        )
    with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
        await admin.execute("DELETE FROM public.audit_log WHERE id = $1", audit_id)


async def test_answer_versions_are_immutable(admin: asyncpg.Connection, seed: Seed) -> None:
    with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
        await admin.execute(
            "UPDATE public.answer_versions SET content = 'rewritten history' WHERE id = $1",
            seed.version_a,
        )
    with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
        await admin.execute("DELETE FROM public.answer_versions WHERE id = $1", seed.version_a)
    # Tenants cannot write versions at all (service-role flow only).
    async with tenant_session(seed.org_a, seed.user_a) as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO public.answer_versions (answer_id, org_id, version, content) "
                "VALUES ($1, $2, 2, 'forged version')",
                seed.answer_a,
                seed.org_a,
            )
    # And B cannot read A's history.
    async with tenant_session(seed.org_b, seed.user_b) as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.answer_versions WHERE id = $1", seed.version_a
        )
    assert row is None


async def test_answer_with_history_cannot_be_deleted(admin: asyncpg.Connection, seed: Seed) -> None:
    """ON DELETE RESTRICT: recorded history pins its answer."""
    with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
        await admin.execute("DELETE FROM public.answers WHERE id = $1", seed.answer_a)


# -- the repository layer (belt) on top of RLS (braces) -------------------------------


class DocumentsRepository(TenantScopedRepository):
    table = "documents"
    order_column = "ingested_at"
    allow_shared_rows = True


@pytest.fixture
async def pool() -> AsyncIterator[asyncpg.Pool[asyncpg.Record]]:
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        yield pool
    finally:
        await pool.close()


async def test_repository_scopes_every_operation_by_tenant(
    pool: asyncpg.Pool[asyncpg.Record], seed: Seed
) -> None:
    repo = DocumentsRepository(pool)

    # B cannot fetch A's document — indistinguishable from nonexistent.
    assert await repo.fetch_by_id(seed.org_b, seed.doc_a) is None
    own = await repo.fetch_by_id(seed.org_a, seed.doc_a)
    assert own is not None and own["id"] == seed.doc_a

    # Shared corpus: visible to both, but only when explicitly asked for.
    assert await repo.fetch_by_id(seed.org_b, seed.doc_shared) is None
    for org in (seed.org_a, seed.org_b):
        shared = await repo.fetch_by_id(org, seed.doc_shared, include_shared=True)
        assert shared is not None

    # Writes land in the caller's org and stay there.
    created = await repo.insert(
        seed.org_b,
        {
            "source_type": "uploaded",
            "title": "B upload via repo",
            "content_hash": f"hash-repo-{uuid4().hex}",
        },
        user_id=seed.user_b,
    )
    assert created["org_id"] == seed.org_b
    assert await repo.fetch_by_id(seed.org_a, created["id"]) is None

    # Cross-tenant update/delete through the repo: no-ops.
    assert await repo.update_by_id(seed.org_a, created["id"], {"title": "stolen"}) is None
    assert await repo.delete_by_id(seed.org_a, created["id"]) is False
    assert await repo.delete_by_id(seed.org_b, created["id"]) is True


async def test_repository_counts_are_tenant_scoped(
    pool: asyncpg.Pool[asyncpg.Record], seed: Seed
) -> None:
    repo = DocumentsRepository(pool)
    assert await repo.count(seed.org_a) == 1
    assert await repo.count(seed.org_b) == 1
    assert await repo.count(seed.org_a, include_shared=True) >= 2
    page = await repo.fetch_page(seed.org_a, include_shared=False)
    assert [r["id"] for r in page] == [seed.doc_a]
