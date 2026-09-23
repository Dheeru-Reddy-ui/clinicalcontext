"""End-to-end Ask flow: guardrails → agent graph → persistence (real DB + corpus).

Runs the offline backend (heuristic reasoner + local embedder/reranker) against
the seeded corpus, so it exercises the whole pipeline without any API key.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.guardrails.phi import WITHHELD_TEXT
from app.services.ask import AskService

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (needs a running Postgres with the seeded corpus)",
)


@pytest.fixture
async def env(
    migrated_database: str,
) -> AsyncIterator[tuple[asyncpg.Pool[asyncpg.Record], asyncpg.Connection, UUID, UUID]]:
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)
    admin = await asyncpg.connect(TEST_DATABASE_URL)
    org_id = await admin.fetchval(
        "INSERT INTO public.organizations (name, slug) VALUES ('AskOrg', $1) RETURNING id",
        f"ask-{uuid4().hex[:8]}",
    )
    user_id = await admin.fetchval(
        "INSERT INTO auth.users (email) VALUES ($1) RETURNING id",
        f"ask-{uuid4().hex[:8]}@cc-tests.org",
    )
    await admin.execute(
        "INSERT INTO public.profiles (id, org_id, role) VALUES ($1, $2, 'clinician')",
        user_id,
        org_id,
    )
    try:
        yield pool, admin, org_id, user_id
    finally:
        await admin.execute("SET session_replication_role = 'replica'")
        await admin.execute("DELETE FROM public.audit_log WHERE org_id = $1", org_id)
        await admin.execute("RESET session_replication_role")
        await admin.execute("DELETE FROM public.organizations WHERE id = $1", org_id)
        await admin.execute("DELETE FROM auth.users WHERE id = $1", user_id)
        await admin.close()
        await pool.close()


async def test_ask_streams_events_and_persists_answer(
    env: tuple[asyncpg.Pool[asyncpg.Record], asyncpg.Connection, UUID, UUID],
) -> None:
    pool, admin, org_id, user_id = env
    events = [
        e
        async for e in AskService(pool).ask(
            query="What is the evidence for anticoagulation in atrial fibrillation?",
            org_id=org_id,
            user_id=user_id,
        )
    ]
    stages = [e["stage"] for e in events]

    assert stages[0] == "accepted"
    assert stages[-1] in ("result", "blocked")
    # The reasoning was made visible (Phase-8 streaming requirement).
    assert "searching" in stages
    result_event = next(e for e in events if e["stage"] == "result")
    query_id = UUID(result_event["data"]["query_id"])

    # The query and an answer row were persisted for the tenant.
    query_row = await admin.fetchrow(
        "SELECT status FROM public.queries WHERE id = $1 AND org_id = $2", query_id, org_id
    )
    assert query_row is not None and query_row["status"] == "completed"
    answer_count = await admin.fetchval(
        "SELECT count(*) FROM public.answers WHERE query_id = $1", query_id
    )
    assert answer_count == 1


async def test_ask_blocks_phi_before_the_graph(
    env: tuple[asyncpg.Pool[asyncpg.Record], asyncpg.Connection, UUID, UUID],
) -> None:
    pool, admin, org_id, user_id = env
    events = [
        e
        async for e in AskService(pool).ask(
            query="What anticoagulant for patient John Smith, DOB 03/14/1982?",
            org_id=org_id,
            user_id=user_id,
        )
    ]
    stages = [e["stage"] for e in events]
    assert "blocked" in stages
    # Blocked before any retrieval/graph work.
    assert "searching" not in stages
    blocked = next(e for e in events if e["stage"] == "blocked")
    assert blocked["data"]["blocked_by"] == "phi"

    # No answer row was created for a blocked query.
    query_id = UUID(events[0]["data"]["query_id"])
    answer_count = await admin.fetchval(
        "SELECT count(*) FROM public.answers WHERE query_id = $1", query_id
    )
    assert answer_count == 0
    # The block is audited.
    audit = await admin.fetchval(
        "SELECT count(*) FROM public.audit_log "
        "WHERE org_id = $1 AND action = 'guardrail.pre_retrieval'",
        org_id,
    )
    assert audit >= 1

    # And nothing that was blocked is kept. The query row and the session
    # title are written before the gate runs, and until this was fixed they
    # kept the name and date of birth word for word — where the autocomplete
    # and the dashboard's top questions could show them to colleagues.
    stored = await admin.fetchrow(
        "SELECT q.raw_query, q.contextualized_query, s.title "
        "FROM public.queries q JOIN public.query_sessions s ON s.id = q.session_id "
        "WHERE q.id = $1",
        query_id,
    )
    assert stored is not None
    assert stored["raw_query"] == WITHHELD_TEXT
    assert stored["contextualized_query"] is None
    assert stored["title"] == WITHHELD_TEXT
    everything = " ".join(str(v) for v in stored.values())
    assert "Smith" not in everything and "1982" not in everything
    # Nor is it echoed into the events kept for idempotent replay.
    accepted = events[0]["data"]["contextualized_query"]
    assert accepted == WITHHELD_TEXT


async def test_a_follow_up_to_a_withheld_question_is_not_contextualized_against_it(
    env: tuple[asyncpg.Pool[asyncpg.Record], asyncpg.Connection, UUID, UUID],
) -> None:
    pool, _admin, org_id, user_id = env
    first = [
        e
        async for e in AskService(pool).ask(
            query="What anticoagulant for patient John Smith, DOB 03/14/1982?",
            org_id=org_id,
            user_id=user_id,
        )
    ]
    session_id = UUID(first[0]["data"]["session_id"])
    follow = [
        e
        async for e in AskService(pool).ask(
            query="what about in pregnancy?",
            org_id=org_id,
            user_id=user_id,
            session_id=session_id,
        )
    ]
    # The follow-up is taken as it is: the placeholder is not a question to
    # build on, and the withheld text is not there to be recovered.
    assert follow[0]["data"]["contextualized_query"] == "what about in pregnancy?"


async def test_migration_024_rewrites_rows_written_before_the_fix(
    env: tuple[asyncpg.Pool[asyncpg.Record], asyncpg.Connection, UUID, UUID],
) -> None:
    pool, admin, org_id, user_id = env
    leaked = "What anticoagulant for patient John Smith, DOB 03/14/1982?"
    events = [e async for e in AskService(pool).ask(query=leaked, org_id=org_id, user_id=user_id)]
    query_id = UUID(events[0]["data"]["query_id"])
    session_id = UUID(events[0]["data"]["session_id"])
    # Put the row back the way the code before the fix wrote it.
    await admin.execute("UPDATE public.queries SET raw_query = $2 WHERE id = $1", query_id, leaked)
    await admin.execute(
        "UPDATE public.query_sessions SET title = $2 WHERE id = $1", session_id, leaked[:120]
    )

    migration = (
        Path(__file__).resolve().parents[1] / "migrations" / "024_withhold_phi_text.sql"
    ).read_text(encoding="utf-8")
    await admin.execute(migration)
    await admin.execute(migration)  # idempotent

    raw = await admin.fetchval("SELECT raw_query FROM public.queries WHERE id = $1", query_id)
    title = await admin.fetchval(
        "SELECT title FROM public.query_sessions WHERE id = $1", session_id
    )
    assert raw == WITHHELD_TEXT
    assert title == WITHHELD_TEXT
