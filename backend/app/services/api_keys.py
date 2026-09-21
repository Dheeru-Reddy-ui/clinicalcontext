"""API-key generation, hashing, verification, and lifecycle.

Keys are shown once at creation; only a SHA-256 hash and a display prefix are
stored. Verification hashes the presented key and looks up the (non-revoked)
row, bumping ``last_used_at``. Scopes gate what a key may do
(read < query < full).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from app.repositories.base import tenant_connection
from app.schemas.api_keys import ApiKeyCreatedOut, ApiKeyOut, ApiKeyScope

if TYPE_CHECKING:
    import asyncpg

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

_KEY_BYTES = 24  # 32-char urlsafe token
_PREFIX = "cck_"
_PREFIX_DISPLAY_LEN = 12  # "cck_" + first 8 chars, shown in the UI


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class ApiKeyContext:
    key_id: UUID
    org_id: UUID
    scopes: list[str]
    prefix: str
    created_by: UUID | None


# scope → the org_role we surface for RBAC-compatible endpoints.
_SCOPE_ROLE = {"full": "owner", "query": "clinician", "read": "viewer"}


def scope_role(scopes: list[str]) -> str:
    for scope in ("full", "query", "read"):
        if scope in scopes:
            return _SCOPE_ROLE[scope]
    return "viewer"


async def resolve_api_key(pool: DbPool, presented: str) -> ApiKeyContext | None:
    """Verify a presented API key. Returns its context, or None if unknown/revoked."""
    if not presented.startswith(_PREFIX):
        return None
    row = await pool.fetchrow(
        "SELECT id, org_id, scopes, key_prefix, created_by, revoked_at "
        "FROM public.api_keys WHERE key_hash = $1",
        _hash(presented),
    )
    if row is None or row["revoked_at"] is not None:
        return None
    await pool.execute("UPDATE public.api_keys SET last_used_at = now() WHERE id = $1", row["id"])
    return ApiKeyContext(
        key_id=row["id"],
        org_id=row["org_id"],
        scopes=list(row["scopes"]),
        prefix=row["key_prefix"],
        created_by=row["created_by"],
    )


class ApiKeyService:
    def __init__(self, pool: DbPool) -> None:
        self._pool = pool

    async def create(
        self, *, org_id: UUID, created_by: UUID, name: str, scopes: list[ApiKeyScope]
    ) -> ApiKeyCreatedOut:
        raw = _PREFIX + secrets.token_urlsafe(_KEY_BYTES)
        prefix = raw[:_PREFIX_DISPLAY_LEN]
        async with tenant_connection(self._pool, org_id, created_by) as conn:
            row = await conn.fetchrow(
                "INSERT INTO public.api_keys "
                "(org_id, name, key_hash, key_prefix, scopes, created_by) "
                "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
                org_id,
                name,
                _hash(raw),
                prefix,
                list(scopes),
                created_by,
            )
        assert row is not None
        return ApiKeyCreatedOut(key=raw, **_row_to_out(row).model_dump())

    async def list_keys(self, *, org_id: UUID, user_id: UUID) -> list[ApiKeyOut]:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            rows = await conn.fetch(
                "SELECT * FROM public.api_keys WHERE org_id = $1 ORDER BY created_at DESC",
                org_id,
            )
        return [_row_to_out(r) for r in rows]

    async def revoke(self, *, org_id: UUID, user_id: UUID, key_id: UUID) -> bool:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            status = await conn.execute(
                "UPDATE public.api_keys SET revoked_at = now() "
                "WHERE id = $1 AND org_id = $2 AND revoked_at IS NULL",
                key_id,
                org_id,
            )
        return status.endswith("1")


def _row_to_out(row: asyncpg.Record) -> ApiKeyOut:
    created_at: datetime = row["created_at"]
    return ApiKeyOut(
        id=row["id"],
        name=row["name"],
        key_prefix=row["key_prefix"],
        scopes=list(row["scopes"]),
        last_used_at=row["last_used_at"],
        revoked_at=row["revoked_at"],
        created_at=created_at,
    )
