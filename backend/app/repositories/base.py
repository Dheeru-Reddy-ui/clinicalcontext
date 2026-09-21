"""Tenant-scoped data access foundation.

Two independent layers keep tenants apart, and this module implements both
ends of the bargain:

1. **RLS is the real boundary** (migrations/006_rls.sql). ``tenant_connection``
   runs every repository query inside a transaction set to role
   ``authenticated`` with the caller's JWT claims in ``request.jwt.claims`` —
   exactly the context Supabase gives a request — so Postgres itself filters
   every row regardless of what SQL this process sends.
2. **The repository never issues an unscoped query anyway** (belt and
   braces). Every method takes ``tenant_id`` and injects the tenant predicate
   into the SQL; there is no code path that queries a tenant-owned table
   without it.

Feature repositories subclass :class:`TenantScopedRepository` and add their
own queries on top of ``tenant_connection`` — never raw pool access.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, ClassVar
from uuid import UUID

import asyncpg
from asyncpg.pool import PoolConnectionProxy

from app.core.errors import TenantIsolationError

# Repository methods accept either a bare connection (tests, scripts) or a
# pool-proxied one (request handling) — the query surface is identical.
type PgConnection = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _validate_identifier(name: str, *, kind: str) -> str:
    """SQL identifiers come only from class definitions, never user input —
    this guard turns a future mistake into a loud boot-time failure."""
    if not _IDENTIFIER.match(name):
        raise ValueError(f"invalid SQL {kind}: {name!r}")
    return name


@asynccontextmanager
async def tenant_connection(
    pool: asyncpg.Pool[asyncpg.Record],
    tenant_id: UUID,
    user_id: UUID | None = None,
) -> AsyncIterator[PoolConnectionProxy[asyncpg.Record]]:
    """A connection whose transaction runs under the tenant's RLS context.

    ``SET LOCAL`` scopes both the role and the claims to this transaction, so
    the connection returns to the pool with no tenant state attached.
    """
    claims: dict[str, Any] = {"role": "authenticated", "org_id": str(tenant_id)}
    if user_id is not None:
        claims["sub"] = str(user_id)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE authenticated")
        await conn.fetchval(
            "SELECT set_config('request.jwt.claims', $1, true)",
            json.dumps(claims),
        )
        yield conn


class TenantScopedRepository:
    """Base class for all tenant-owned tables.

    Subclasses set ``table`` (and optionally ``allow_shared_rows`` for the
    corpus tables where ``org_id IS NULL`` marks the shared public corpus).
    """

    table: ClassVar[str]
    tenant_column: ClassVar[str] = "org_id"
    order_column: ClassVar[str] = "created_at"
    # Only documents/chunks set this: org_id IS NULL rows (shared corpus) are
    # readable but never writable through this layer.
    allow_shared_rows: ClassVar[bool] = False

    def __init__(self, pool: asyncpg.Pool[asyncpg.Record]) -> None:
        _validate_identifier(self.table, kind="table name")
        _validate_identifier(self.tenant_column, kind="column name")
        _validate_identifier(self.order_column, kind="column name")
        self._pool = pool

    # -- predicate helpers -----------------------------------------------------

    def _tenant_predicate(self, param: int, *, include_shared: bool) -> str:
        if include_shared and self.allow_shared_rows:
            return f"({self.tenant_column} = ${param} OR {self.tenant_column} IS NULL)"
        return f"{self.tenant_column} = ${param}"

    # -- generic operations ------------------------------------------------------

    async def fetch_by_id(
        self,
        tenant_id: UUID,
        row_id: UUID,
        *,
        include_shared: bool = False,
        user_id: UUID | None = None,
    ) -> asyncpg.Record | None:
        query = (
            f"SELECT * FROM {self.table} "
            f"WHERE id = $1 AND {self._tenant_predicate(2, include_shared=include_shared)}"
        )
        async with tenant_connection(self._pool, tenant_id, user_id) as conn:
            return await conn.fetchrow(query, row_id, tenant_id)

    async def fetch_page(
        self,
        tenant_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        include_shared: bool = False,
        user_id: UUID | None = None,
    ) -> list[asyncpg.Record]:
        query = (
            f"SELECT * FROM {self.table} "
            f"WHERE {self._tenant_predicate(1, include_shared=include_shared)} "
            f"ORDER BY {self.order_column} DESC LIMIT $2 OFFSET $3"
        )
        async with tenant_connection(self._pool, tenant_id, user_id) as conn:
            return list(await conn.fetch(query, tenant_id, limit, offset))

    async def count(
        self,
        tenant_id: UUID,
        *,
        include_shared: bool = False,
        user_id: UUID | None = None,
    ) -> int:
        query = (
            f"SELECT count(*) FROM {self.table} "
            f"WHERE {self._tenant_predicate(1, include_shared=include_shared)}"
        )
        async with tenant_connection(self._pool, tenant_id, user_id) as conn:
            result = await conn.fetchval(query, tenant_id)
            return int(result) if result is not None else 0

    async def insert(
        self,
        tenant_id: UUID,
        values: Mapping[str, Any],
        *,
        user_id: UUID | None = None,
    ) -> asyncpg.Record:
        """Insert a row into the caller's tenant. The tenant column is always
        taken from ``tenant_id`` — a value smuggled in ``values`` is rejected,
        not silently overwritten."""
        if self.tenant_column in values:
            raise TenantIsolationError(
                f"{self.tenant_column} must not appear in insert values; "
                "it is derived from the authenticated tenant",
                context={"table": self.table},
            )
        columns = [_validate_identifier(c, kind="column name") for c in values]
        columns.append(self.tenant_column)
        params: list[Any] = [*values.values()]
        params.append(tenant_id)
        placeholders = ", ".join(f"${i}" for i in range(1, len(params) + 1))
        query = (
            f"INSERT INTO {self.table} ({', '.join(columns)}) VALUES ({placeholders}) RETURNING *"
        )
        async with tenant_connection(self._pool, tenant_id, user_id) as conn:
            row = await conn.fetchrow(query, *params)
        if row is None:  # pragma: no cover — INSERT..RETURNING always yields a row
            raise RuntimeError(f"insert into {self.table} returned no row")
        return row

    async def update_by_id(
        self,
        tenant_id: UUID,
        row_id: UUID,
        values: Mapping[str, Any],
        *,
        user_id: UUID | None = None,
    ) -> asyncpg.Record | None:
        """Update a row within the tenant. Returns None when the row does not
        exist *for this tenant* — cross-tenant targets look identical to
        missing rows, by design."""
        if not values:
            raise ValueError("update_by_id requires at least one column")
        if self.tenant_column in values:
            raise TenantIsolationError(
                f"{self.tenant_column} cannot be changed through the repository",
                context={"table": self.table},
            )
        assignments = ", ".join(
            f"{_validate_identifier(col, kind='column name')} = ${i}"
            for i, col in enumerate(values, start=1)
        )
        next_param = len(values) + 1
        query = (
            f"UPDATE {self.table} SET {assignments} "
            f"WHERE id = ${next_param} AND {self.tenant_column} = ${next_param + 1} "
            f"RETURNING *"
        )
        async with tenant_connection(self._pool, tenant_id, user_id) as conn:
            return await conn.fetchrow(query, *values.values(), row_id, tenant_id)

    async def delete_by_id(
        self,
        tenant_id: UUID,
        row_id: UUID,
        *,
        user_id: UUID | None = None,
    ) -> bool:
        query = f"DELETE FROM {self.table} WHERE id = $1 AND {self.tenant_column} = $2"
        async with tenant_connection(self._pool, tenant_id, user_id) as conn:
            status = await conn.execute(query, row_id, tenant_id)
        return status == "DELETE 1"
