"""Phase 13: the public demo query — the real pipeline, no login, one
allowlisted question at a time, rate-limited per visitor."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.services.demo import DEMO_ORG_ID, DEMO_QUESTIONS, DEMO_USER_ID
from app.services.semantic_cache import clear_semantic_cache
from tests.conftest import ApiEnv


async def _reset_demo_limits(env: ApiEnv) -> None:
    # The demo tenant's id is fixed, so its Redis keys (rate limits and the
    # semantic cache) are shared with any other environment on this Redis —
    # cleared before and after so a test never leaks an answer computed on
    # the test corpus into a development server, or the other way round.
    await env.redis.delete("rl:demo:testclient", "rl:demo:127.0.0.1", "rl:demo:all")
    await clear_semantic_cache(env.redis, DEMO_ORG_ID)


@pytest.fixture(autouse=True)
async def _clean_demo_keys(env: ApiEnv) -> AsyncIterator[None]:
    await _reset_demo_limits(env)
    yield
    await _reset_demo_limits(env)


async def test_demo_lists_only_its_allowlist(env: ApiEnv) -> None:
    response = await env.client.get("/api/public/demo/questions")
    assert response.status_code == 200
    questions = response.json()["questions"]
    assert [q["id"] for q in questions] == [q.id for q in DEMO_QUESTIONS]
    assert all(q["shows"] in ("contradiction", "cited answer") for q in questions)


async def test_demo_runs_the_real_pipeline_as_the_demo_tenant(env: ApiEnv) -> None:
    # Migration 022 seeded the tenant: no configuration, no credential.
    row = await env.admin.fetchrow(
        "SELECT o.slug, p.role FROM public.organizations o "
        "JOIN public.profiles p ON p.org_id = o.id WHERE o.id = $1 AND p.id = $2",
        DEMO_ORG_ID,
        DEMO_USER_ID,
    )
    assert row is not None and row["slug"] == "public-demo" and row["role"] == "viewer"

    picked = next(q for q in DEMO_QUESTIONS if q.shows == "contradiction")
    response = await env.client.post("/api/public/demo", json={"question_id": picked.id})
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["question"] == picked.question
    assert out["blocked"] is None, "an allowlisted literature question must never be blocked"
    assert out["answer"] and not out["abstained"]
    assert out["citations"], "the demo answer is cited like any other"
    assert all("chunk_id" not in c and "document_id" not in c for c in out["citations"]), (
        "no internal ids on the public surface"
    )
    assert {"detected", "positions", "axis", "explanation"} <= set(out["contradiction"])
    if out["contradiction"]["detected"]:
        assert len(out["contradiction"]["positions"]) >= 2
    # The run is ordinary tenant data: a query row under the demo org, and
    # its provider calls in the cost ledger.
    queries = await env.admin.fetchval(
        "SELECT count(*) FROM public.queries WHERE org_id = $1 AND user_id = $2",
        DEMO_ORG_ID,
        DEMO_USER_ID,
    )
    assert queries >= 1
    ledger = await env.admin.fetchval(
        "SELECT count(*) FROM public.cost_events WHERE org_id = $1", DEMO_ORG_ID
    )
    assert ledger >= 1


async def test_demo_rejects_free_text_and_unknown_ids(env: ApiEnv) -> None:
    response = await env.client.post("/api/public/demo", json={"question_id": "not-a-demo"})
    assert response.status_code == 404
    response = await env.client.post(
        "/api/public/demo", json={"question": "My patient with SSN 123-45-6789 has chest pain"}
    )
    assert response.status_code == 422, "only an allowlist id is accepted"


async def test_demo_is_rate_limited_per_visitor(env: ApiEnv) -> None:
    # Six is the per-visitor budget; the seventh in the same minute is refused
    # before the pipeline runs. A cache hit keeps the first six cheap.
    picked = DEMO_QUESTIONS[-1]
    statuses = []
    for _ in range(7):
        response = await env.client.post("/api/public/demo", json={"question_id": picked.id})
        statuses.append(response.status_code)
    assert statuses[:6] == [200] * 6, statuses
    assert statuses[6] == 429
    assert "Retry-After" in {k.title(): v for k, v in response.headers.items()}
