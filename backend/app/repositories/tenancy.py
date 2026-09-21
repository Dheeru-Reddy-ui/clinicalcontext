"""Data access for organizations, profiles, and invites.

Every method takes an explicit connection so the service layer controls
transaction boundaries — a mutation and its audit row always commit together.
Methods that serve tenant requests still carry explicit org predicates (belt)
even when the connection is RLS-scoped (braces); methods documented as
*service context* run provisioning flows that RLS intentionally reserves for
the service role (org creation, invite acceptance, role administration).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from app.repositories.base import PgConnection
from app.schemas.tenancy import OrgRole


class TenancyRepository:
    # -- identity (local shim only; see Settings.auth_identity_mirror) ------------

    async def mirror_identity(
        self, conn: PgConnection, *, user_id: UUID, email: str | None, full_name: str | None
    ) -> None:
        """Ensure the shim's auth.users has a row for a GoTrue-verified subject.

        Idempotent and never overwrites: the identity provider is the source of
        truth, this only satisfies the profiles → auth.users foreign key when
        auth lives in a different database from the data.
        """
        await conn.execute(
            "INSERT INTO auth.users (id, email, raw_user_meta_data) "
            "VALUES ($1, $2, $3::jsonb) ON CONFLICT DO NOTHING",
            user_id,
            email,
            json.dumps({"full_name": full_name} if full_name else {}),
        )

    # -- profiles ---------------------------------------------------------------

    async def get_profile(self, conn: PgConnection, user_id: UUID) -> asyncpg.Record | None:
        return await conn.fetchrow(
            "SELECT id, org_id, role, full_name, specialty, created_at "
            "FROM public.profiles WHERE id = $1",
            user_id,
        )

    async def create_profile(
        self,
        conn: PgConnection,
        *,
        user_id: UUID,
        org_id: UUID,
        role: OrgRole,
        full_name: str | None,
    ) -> asyncpg.Record:
        row = await conn.fetchrow(
            "INSERT INTO public.profiles (id, org_id, role, full_name) "
            "VALUES ($1, $2, $3, $4) RETURNING *",
            user_id,
            org_id,
            role,
            full_name,
        )
        assert row is not None  # INSERT .. RETURNING
        return row

    async def list_members(self, conn: PgConnection, org_id: UUID) -> list[asyncpg.Record]:
        """Service context: joins auth.users for emails (org-visible data);
        the explicit org predicate is the scoping."""
        rows = await conn.fetch(
            "SELECT p.id, p.org_id, p.role, p.full_name, p.specialty, p.created_at, u.email "
            "FROM public.profiles p LEFT JOIN auth.users u ON u.id = p.id "
            "WHERE p.org_id = $1 ORDER BY p.created_at",
            org_id,
        )
        return list(rows)

    async def count_owners(self, conn: PgConnection, org_id: UUID) -> int:
        value = await conn.fetchval(
            "SELECT count(*) FROM public.profiles WHERE org_id = $1 AND role = 'owner'",
            org_id,
        )
        return int(value) if value is not None else 0

    async def update_member_role(
        self,
        conn: PgConnection,
        *,
        org_id: UUID,
        user_id: UUID,
        role: OrgRole,
    ) -> asyncpg.Record | None:
        """Service context (profiles RLS only allows self-update); the org
        predicate confines the write to the caller's organization."""
        return await conn.fetchrow(
            "UPDATE public.profiles SET role = $3 WHERE id = $1 AND org_id = $2 RETURNING *",
            user_id,
            org_id,
            role,
        )

    # -- organizations -------------------------------------------------------------

    async def get_org(self, conn: PgConnection, org_id: UUID) -> asyncpg.Record | None:
        return await conn.fetchrow(
            "SELECT id, name, slug, plan, created_at FROM public.organizations WHERE id = $1",
            org_id,
        )

    async def create_org(self, conn: PgConnection, *, name: str, slug: str) -> asyncpg.Record:
        """Service context: org creation happens before the creator has a tenant."""
        row = await conn.fetchrow(
            "INSERT INTO public.organizations (name, slug) VALUES ($1, $2) RETURNING *",
            name,
            slug,
        )
        assert row is not None
        return row

    # -- invites ---------------------------------------------------------------------

    async def create_invite(
        self,
        conn: PgConnection,
        *,
        org_id: UUID,
        email: str,
        role: OrgRole,
        token: str,
        expires_at: datetime,
    ) -> asyncpg.Record:
        row = await conn.fetchrow(
            "INSERT INTO public.org_invites (org_id, email, role, token, expires_at) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING *",
            org_id,
            email,
            role,
            token,
            expires_at,
        )
        assert row is not None
        return row

    async def list_invites(self, conn: PgConnection, org_id: UUID) -> list[asyncpg.Record]:
        rows = await conn.fetch(
            "SELECT id, org_id, email, role, expires_at, accepted_at, created_at "
            "FROM public.org_invites WHERE org_id = $1 ORDER BY created_at DESC",
            org_id,
        )
        return list(rows)

    async def get_live_invite_by_token(
        self, conn: PgConnection, token: str
    ) -> asyncpg.Record | None:
        """Service context: the acceptor has no org yet. Only unaccepted,
        unexpired invites are returned — expired/used/unknown all look
        identical to the caller (no validity oracle)."""
        return await conn.fetchrow(
            "SELECT * FROM public.org_invites "
            "WHERE token = $1 AND accepted_at IS NULL AND expires_at > $2",
            token,
            datetime.now(tz=UTC),
        )

    async def mark_invite_accepted(self, conn: PgConnection, invite_id: UUID) -> None:
        await conn.execute(
            "UPDATE public.org_invites SET accepted_at = $2 WHERE id = $1",
            invite_id,
            datetime.now(tz=UTC),
        )

    # -- audit -------------------------------------------------------------------------

    async def insert_audit(
        self,
        conn: PgConnection,
        *,
        org_id: UUID,
        user_id: UUID | None,
        action: str,
        resource_type: str | None = None,
        resource_id: UUID | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Called inside the same transaction as the mutation it describes."""
        await conn.execute(
            "INSERT INTO public.audit_log (org_id, user_id, action, resource_type, "
            "resource_id, payload) VALUES ($1, $2, $3, $4, $5, $6::jsonb)",
            org_id,
            user_id,
            action,
            resource_type,
            resource_id,
            json.dumps(payload or {}),
        )
