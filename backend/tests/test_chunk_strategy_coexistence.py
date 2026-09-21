"""Migration 008: multiple chunking strategies coexist for one document."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import asyncpg
import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (needs a running Postgres; see README)",
)


@pytest.fixture
async def conn(migrated_database: str) -> AsyncIterator[asyncpg.Connection]:
    connection = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        yield connection
    finally:
        await connection.close()


async def test_same_document_can_hold_multiple_strategies(conn: asyncpg.Connection) -> None:
    salt = uuid4().hex[:8]
    document_id = await conn.fetchval(
        "INSERT INTO public.documents (org_id, source_type, title, content_hash) "
        "VALUES (NULL, 'pubmed', $1, $2) RETURNING id",
        f"Coexistence test {salt}",
        f"hash-{salt}",
    )
    try:
        # Same (document_id, chunk_index=0) under two strategies — previously a
        # unique-constraint violation, now permitted by (doc, strategy, index).
        for strategy in ("fixed", "recursive", "semantic", "structural"):
            await conn.execute(
                "INSERT INTO public.chunks "
                "(document_id, chunk_index, content, token_count, strategy) "
                "VALUES ($1, 0, $2, 10, $3)",
                document_id,
                f"{strategy} chunk zero",
                strategy,
            )

        strategies = await conn.fetch(
            "SELECT strategy FROM public.chunks WHERE document_id = $1 ORDER BY strategy",
            document_id,
        )
        assert [r["strategy"] for r in strategies] == [
            "fixed",
            "recursive",
            "semantic",
            "structural",
        ]

        # The uniqueness now bites only within a strategy.
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO public.chunks "
                "(document_id, chunk_index, content, token_count, strategy) "
                "VALUES ($1, 0, 'dup', 10, 'fixed')",
                document_id,
            )
    finally:
        await conn.execute("DELETE FROM public.documents WHERE id = $1", document_id)
