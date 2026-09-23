"""Tenancy business logic: bootstrap, orgs, invites, members, roles.

Transaction discipline: every mutation and its audit_log row are written on
the same connection inside the same transaction — an action that happened is
an action that was audited, atomically.

Context discipline: reads/writes that RLS policies allow run through
``tenant_connection`` (authenticated role); provisioning flows that RLS
deliberately reserves for the service role (org creation, invite acceptance,
role administration) run on the service connection with explicit org
predicates and route-level RBAC as the guard.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg
import httpx
import structlog

from app.config import get_settings
from app.core.errors import (
    NotFoundError,
    PermissionDeniedError,
)
from app.core.security import CurrentUser
from app.core.supabase_keys import service_headers
from app.repositories.base import PgConnection, tenant_connection
from app.repositories.tenancy import TenancyRepository
from app.schemas.tenancy import (
    InviteOut,
    MemberOut,
    MembersOut,
    MeOut,
    OrgOut,
    OrgRole,
)

logger = structlog.stdlib.get_logger("app.services.tenancy")

_INVITE_TTL = timedelta(days=7)
_SLUG_MAX_LENGTH = 40


class AlreadyInOrgError(PermissionDeniedError):
    error_code = "already_in_organization"
    status_code = 409
    default_message = "The user already belongs to an organization."


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:_SLUG_MAX_LENGTH].strip("-")
    if len(slug) < 3:
        slug = f"org-{secrets.token_hex(3)}"
    return slug


class TenancyService:
    def __init__(self, pool: asyncpg.Pool[asyncpg.Record]) -> None:
        self._pool = pool
        self._repo = TenancyRepository()

    # -- reads ----------------------------------------------------------------------

    async def get_me(self, user: CurrentUser) -> MeOut:
        if user.org_id is None or user.role is None:  # defensive; routes gate on org
            raise NotFoundError("user has no organization")
        async with tenant_connection(self._pool, user.org_id, user.user_id) as conn:
            org = await self._repo.get_org(conn, user.org_id)
        if org is None:  # pragma: no cover — FK guarantees the org exists
            raise NotFoundError("organization not found")
        return MeOut(
            user_id=user.user_id,
            email=user.email,
            full_name=user.full_name,
            specialty=user.specialty,
            role=user.role,
            org=OrgOut(**dict(org)),
        )

    async def list_members(self, user: CurrentUser) -> MembersOut:
        assert user.org_id is not None
        # Service context: profiles are joined with auth.users for emails,
        # which the authenticated role cannot read. Scoped by org explicitly.
        async with self._pool.acquire() as conn:
            rows = await self._repo.list_members(conn, user.org_id)
        members = [
            MemberOut(
                user_id=row["id"],
                email=row["email"],
                full_name=row["full_name"],
                specialty=row["specialty"],
                role=row["role"],
                joined_at=row["created_at"],
            )
            for row in rows
        ]
        return MembersOut(members=members)

    async def list_invites(self, user: CurrentUser) -> list[InviteOut]:
        assert user.org_id is not None
        async with tenant_connection(self._pool, user.org_id, user.user_id) as conn:
            rows = await self._repo.list_invites(conn, user.org_id)
        return [
            InviteOut(
                id=row["id"],
                email=row["email"],
                role=row["role"],
                token=None,  # raw token is shown exactly once, at creation
                expires_at=row["expires_at"],
                accepted_at=row["accepted_at"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    # -- provisioning ------------------------------------------------------------------

    async def bootstrap(
        self,
        user: CurrentUser,
        *,
        org_name: str | None,
        invite_token: str | None,
    ) -> MeOut:
        """Idempotent first-login provisioning."""
        if user.is_bootstrapped:
            return await self.get_me(user)
        if invite_token:
            return await self.accept_invite(user, token=invite_token)
        name = org_name or _default_org_name(user)
        return await self.create_org(user, name=name)

    async def _ensure_identity(self, conn: PgConnection, user: CurrentUser) -> None:
        """Local-shim only: mirror the verified subject into auth.users first."""
        if get_settings().auth_identity_mirror:
            await self._repo.mirror_identity(
                conn, user_id=user.user_id, email=user.email, full_name=user.full_name
            )

    async def create_org(self, user: CurrentUser, *, name: str) -> MeOut:
        if user.is_bootstrapped:
            raise AlreadyInOrgError()
        async with self._pool.acquire() as conn, conn.transaction():
            await self._ensure_identity(conn, user)
            org = await self._create_org_with_unique_slug(conn, name)
            profile = await self._repo.create_profile(
                conn,
                user_id=user.user_id,
                org_id=org["id"],
                role="owner",
                full_name=user.full_name,
            )
            await self._repo.insert_audit(
                conn,
                org_id=org["id"],
                user_id=user.user_id,
                action="org.created",
                resource_type="organization",
                resource_id=org["id"],
                payload={"name": org["name"], "slug": org["slug"]},
            )
        await self._sync_org_claim(user.user_id, org["id"])
        logger.info("org_created", org_id=str(org["id"]))
        return _me_from_rows(user, profile, org)

    async def accept_invite(self, user: CurrentUser, *, token: str) -> MeOut:
        if user.is_bootstrapped:
            raise AlreadyInOrgError()
        async with self._pool.acquire() as conn, conn.transaction():
            invite = await self._repo.get_live_invite_by_token(conn, token)
            if invite is None:
                raise NotFoundError("invite not found, expired, or already used")
            if user.email is None or invite["email"].lower() != user.email.lower():
                # Same response as an unknown token: no oracle for probing
                # whether an invite exists for someone else's address.
                raise NotFoundError("invite not found, expired, or already used")
            org = await self._repo.get_org(conn, invite["org_id"])
            if org is None:  # pragma: no cover — FK guarantees it
                raise NotFoundError("invite not found, expired, or already used")
            await self._ensure_identity(conn, user)
            profile = await self._repo.create_profile(
                conn,
                user_id=user.user_id,
                org_id=invite["org_id"],
                role=invite["role"],
                full_name=user.full_name,
            )
            await self._repo.mark_invite_accepted(conn, invite["id"])
            await self._repo.insert_audit(
                conn,
                org_id=invite["org_id"],
                user_id=user.user_id,
                action="org.invite.accepted",
                resource_type="org_invite",
                resource_id=invite["id"],
                payload={"role": invite["role"]},
            )
        await self._sync_org_claim(user.user_id, invite["org_id"])
        logger.info("invite_accepted", org_id=str(invite["org_id"]))
        return _me_from_rows(user, profile, org)

    async def _create_org_with_unique_slug(self, conn: PgConnection, name: str) -> asyncpg.Record:
        base = _slugify(name)
        for attempt in range(3):
            slug = base if attempt == 0 else f"{base[:32]}-{secrets.token_hex(2)}"
            try:
                # Each attempt gets its own savepoint. Postgres aborts the whole
                # transaction on a failed statement, so retrying a unique
                # violation without one would hit "current transaction is
                # aborted" on the next command rather than trying a new slug —
                # a 500 for the second organisation to pick a given name.
                async with conn.transaction():
                    return await self._repo.create_org(conn, name=name, slug=slug)
            except asyncpg.UniqueViolationError:
                continue
        raise RuntimeError(f"could not allocate a unique slug for {name!r}")

    # -- invites -----------------------------------------------------------------------

    async def create_invite(self, user: CurrentUser, *, email: str, role: OrgRole) -> InviteOut:
        assert user.org_id is not None
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(tz=UTC) + _INVITE_TTL
        async with tenant_connection(self._pool, user.org_id, user.user_id) as conn:
            row = await self._repo.create_invite(
                conn,
                org_id=user.org_id,
                email=email.lower(),
                role=role,
                token=token,
                expires_at=expires_at,
            )
            await self._repo.insert_audit(
                conn,
                org_id=user.org_id,
                user_id=user.user_id,
                action="org.invite.created",
                resource_type="org_invite",
                resource_id=row["id"],
                payload={"email": email.lower(), "role": role},
            )
        return InviteOut(
            id=row["id"],
            email=row["email"],
            role=row["role"],
            token=token,
            expires_at=row["expires_at"],
            accepted_at=None,
            created_at=row["created_at"],
        )

    # -- members --------------------------------------------------------------------------

    async def change_member_role(
        self, actor: CurrentUser, *, member_id: UUID, role: OrgRole
    ) -> MemberOut:
        assert actor.org_id is not None
        async with self._pool.acquire() as conn, conn.transaction():
            target = await conn.fetchrow(
                "SELECT id, org_id, role, full_name, specialty, created_at "
                "FROM public.profiles WHERE id = $1 AND org_id = $2",
                member_id,
                actor.org_id,
            )
            if target is None:
                raise NotFoundError("member not found in this organization")
            if (
                target["role"] == "owner"
                and role != "owner"
                and await self._repo.count_owners(conn, actor.org_id) <= 1
            ):
                raise PermissionDeniedError("cannot demote the last owner of an organization")
            updated = await self._repo.update_member_role(
                conn, org_id=actor.org_id, user_id=member_id, role=role
            )
            assert updated is not None  # row was just read in this transaction
            await self._repo.insert_audit(
                conn,
                org_id=actor.org_id,
                user_id=actor.user_id,
                action="org.member.role_changed",
                resource_type="profile",
                resource_id=member_id,
                payload={"from": target["role"], "to": role},
            )
        return MemberOut(
            user_id=updated["id"],
            email=None,
            full_name=updated["full_name"],
            specialty=updated["specialty"],
            role=updated["role"],
            joined_at=updated["created_at"],
        )

    # -- Supabase claim sync -----------------------------------------------------------------

    async def _sync_org_claim(self, user_id: UUID, org_id: UUID) -> None:
        """Mirror org_id into Supabase app_metadata (best effort).

        Our own RLS path derives the org from the profiles row server-side and
        does not depend on this claim; it exists so direct-to-Supabase access
        (PostgREST, realtime) sees the same org. Unreachable locally — logged,
        never fatal.
        """
        settings = get_settings()
        url = f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.put(
                    url,
                    headers=service_headers(settings),
                    json={"app_metadata": {"org_id": str(org_id)}},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("app_metadata_sync_failed", error=str(exc))


def _default_org_name(user: CurrentUser) -> str:
    if user.full_name:
        return f"{user.full_name}'s organization"
    if user.email:
        return f"{user.email.split('@')[0]}'s organization"
    return "New organization"


def _me_from_rows(user: CurrentUser, profile: asyncpg.Record, org: asyncpg.Record) -> MeOut:
    return MeOut(
        user_id=user.user_id,
        email=user.email,
        full_name=profile["full_name"],
        specialty=profile["specialty"],
        role=profile["role"],
        org=OrgOut(
            id=org["id"],
            name=org["name"],
            slug=org["slug"],
            plan=org["plan"],
            created_at=org["created_at"],
        ),
    )
