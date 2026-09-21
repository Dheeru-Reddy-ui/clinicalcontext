"""Unit tests for the repository guardrails (no database required)."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

import asyncpg
import pytest

from app.core.errors import TenantIsolationError
from app.repositories.base import TenantScopedRepository


class DocumentsRepo(TenantScopedRepository):
    table = "documents"
    order_column = "ingested_at"
    allow_shared_rows = True


class QueriesRepo(TenantScopedRepository):
    table = "queries"


def _unused_pool() -> asyncpg.Pool[asyncpg.Record]:
    """The guards under test fire before any pool access."""
    return cast("asyncpg.Pool[asyncpg.Record]", object())


def test_malicious_table_identifier_rejected_at_construction() -> None:
    class BadRepo(TenantScopedRepository):
        table = "documents; DROP TABLE public.documents--"

    with pytest.raises(ValueError, match="invalid SQL table name"):
        BadRepo(_unused_pool())


def test_tenant_predicate_scopes_by_org() -> None:
    repo = QueriesRepo(_unused_pool())
    assert repo._tenant_predicate(1, include_shared=False) == "org_id = $1"
    # include_shared is meaningless on non-corpus tables and must not widen them.
    assert repo._tenant_predicate(1, include_shared=True) == "org_id = $1"


def test_tenant_predicate_widens_only_for_shared_corpus_tables() -> None:
    repo = DocumentsRepo(_unused_pool())
    assert repo._tenant_predicate(2, include_shared=True) == "(org_id = $2 OR org_id IS NULL)"
    assert repo._tenant_predicate(2, include_shared=False) == "org_id = $2"


async def test_insert_rejects_smuggled_tenant_column() -> None:
    repo = DocumentsRepo(_unused_pool())
    with pytest.raises(TenantIsolationError):
        await repo.insert(uuid4(), {"title": "x", "org_id": uuid4()})


async def test_insert_rejects_malicious_column_names() -> None:
    repo = DocumentsRepo(_unused_pool())
    with pytest.raises(ValueError, match="invalid SQL column name"):
        await repo.insert(uuid4(), {"title = 'x' WHERE 1=1; --": "boom"})


async def test_update_rejects_tenant_column_change() -> None:
    repo = DocumentsRepo(_unused_pool())
    with pytest.raises(TenantIsolationError):
        await repo.update_by_id(uuid4(), uuid4(), {"org_id": uuid4()})


async def test_update_requires_columns() -> None:
    repo = DocumentsRepo(_unused_pool())
    with pytest.raises(ValueError, match="at least one column"):
        await repo.update_by_id(uuid4(), uuid4(), {})
