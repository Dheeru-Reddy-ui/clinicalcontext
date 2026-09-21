"""Phase 9 gate: the API layer end-to-end against real Postgres + Redis.

Everything runs on the offline backend (heuristic reasoner + local embedder/
reranker) against the seeded corpus, so it exercises the whole streaming query
path — semantic cache, idempotency, API keys, rate limiting, comparison mode,
and real (truthfully $0) cost accounting — without any paid API key.

The seven assertions here mirror the Phase 9 gate checklist one-to-one.
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest

from app.retrieval.embed import EmbeddingService
from app.retrieval.embedders import get_embedder
from app.services.ask import AskService
from app.services.semantic_cache import SemanticCache
from tests.conftest import ApiEnv, parse_sse

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "")

pytestmark = pytest.mark.skipif(
    not (TEST_DATABASE_URL and TEST_REDIS_URL),
    reason="TEST_DATABASE_URL/TEST_REDIS_URL not set (needs a running Postgres + Redis)",
)

# A query that reliably grounds against the seeded corpus (verified: grade B).
GROUNDED_QUERY = "How is type 2 diabetes managed with metformin?"


# -- gate 1: POST /v1/queries streams tokens ----------------------------------------


async def test_streaming_query_emits_tokens_then_result(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner()
    response = await env.client.post(
        "/api/v1/queries",
        json={"query": GROUNDED_QUERY},
        headers=env.auth(token),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    stages = [e["stage"] for e in events]
    assert stages[0] == "accepted"
    assert "token" in stages, "answer must stream as tokens"
    assert stages[-1] == "result"

    result = events[-1]["data"]
    assert result["query_id"]
    assert result["cached"] is False
    # Real cost accounting is present on the answer (offline backend → truthful $0).
    assert result["cost_usd"] == 0.0
    assert "input_tokens" in result and "output_tokens" in result


# -- gate 2: same query twice hits the semantic cache -------------------------------


async def test_second_identical_query_served_from_cache(env: ApiEnv) -> None:
    org_id, user_id, _token = await env.new_org_with_owner()
    service = AskService(env.pool, env.redis)

    first = [e async for e in service.ask(query=GROUNDED_QUERY, org_id=org_id, user_id=user_id)]
    first_result = next(e for e in first if e["stage"] == "result")
    assert first_result["data"]["cached"] is False
    assert "searching" in [e["stage"] for e in first], "first run executes the graph"

    second = [e async for e in service.ask(query=GROUNDED_QUERY, org_id=org_id, user_id=user_id)]
    second_stages = [e["stage"] for e in second]
    second_result = next(e for e in second if e["stage"] == "result")

    # Cache flag set, and the graph did NOT run again (latency drop).
    assert second_result["data"]["cached"] is True
    assert second_result["data"]["cost_usd"] == 0.0
    assert "cache_similarity" in second_result["data"]
    assert "searching" not in second_stages, "cache hit must skip retrieval/generation"

    # The served answer is persisted as a cache row (model='cache'), not a re-run.
    second_qid = UUID(second[0]["data"]["query_id"])
    model = await env.admin.fetchval(
        "SELECT model FROM public.answers WHERE query_id = $1", second_qid
    )
    assert model == "cache"


async def test_semantic_cache_threshold_is_enforced(env: ApiEnv) -> None:
    """Unit-level: identical embedding hits; a dissimilar one misses (0.97)."""
    org_id, _user_id, _token = await env.new_org_with_owner()
    cache = SemanticCache(env.redis)
    embedder = EmbeddingService(get_embedder("local"), env.redis)
    vector = await embedder.embed_query("anticoagulation in atrial fibrillation")
    payload = {"answer": "cached answer", "query_type": "therapy"}
    await cache.store(org_id, vector, payload, cost_usd=0.42)

    hit = await cache.lookup(org_id, vector)
    assert hit is not None
    assert hit.similarity >= 0.97
    assert hit.saved_usd == 0.42
    assert hit.payload["answer"] == "cached answer"

    other = await embedder.embed_query("management of community acquired pneumonia in adults")
    assert await cache.lookup(org_id, other) is None


# -- gate 3: Idempotency-Key replay returns identical response, no second run -------


async def test_idempotency_key_replays_without_rerunning(env: ApiEnv) -> None:
    org_id, user_id, _token = await env.new_org_with_owner()
    service = AskService(env.pool, env.redis)

    async def count_queries() -> int:
        value = await env.admin.fetchval(
            "SELECT count(*) FROM public.queries WHERE org_id = $1", org_id
        )
        return int(value)

    key = f"idem-{uuid4().hex}"
    first = [
        e
        async for e in service.ask(
            query=GROUNDED_QUERY, org_id=org_id, user_id=user_id, idempotency_key=key
        )
    ]
    after_first = await count_queries()
    assert after_first == 1

    second = [
        e
        async for e in service.ask(
            query=GROUNDED_QUERY, org_id=org_id, user_id=user_id, idempotency_key=key
        )
    ]
    after_second = await count_queries()

    # No second query row was created — the stored response was replayed verbatim.
    assert after_second == after_first
    assert all(e.get("replayed") is True for e in second)
    assert [{k: v for k, v in e.items() if k != "replayed"} for e in second] == first


# -- gate 4: API-key request works, is separately rate-limited, and is audited ------


async def test_api_key_request_works_is_audited_and_uses_its_own_bucket(env: ApiEnv) -> None:
    org_id, _owner_id, owner_token = await env.new_org_with_owner()

    created = await env.client.post(
        "/api/v1/api-keys",
        json={"name": "integration", "scopes": ["query"]},
        headers=env.auth(owner_token),
    )
    assert created.status_code == 201, created.text
    key = created.json()["key"]
    prefix = created.json()["key_prefix"]
    assert key.startswith("cck_")

    # The creation itself is audited with the (non-secret) prefix.
    create_audit = await env.admin.fetchval(
        "SELECT payload->>'prefix' FROM public.audit_log "
        "WHERE org_id = $1 AND action = 'api_key.created'",
        org_id,
    )
    assert create_audit == prefix

    # A query authenticated purely by the API key succeeds and streams.
    response = await env.client.post(
        "/api/v1/queries", json={"query": GROUNDED_QUERY}, headers={"X-API-Key": key}
    )
    assert response.status_code == 200, response.text
    assert parse_sse(response.text)[0]["stage"] == "accepted"

    # It is audited as api.request, tagged with the key prefix.
    request_prefix = await env.admin.fetchval(
        "SELECT payload->>'api_key_prefix' FROM public.audit_log "
        "WHERE org_id = $1 AND action = 'api.request'",
        org_id,
    )
    assert request_prefix == prefix

    # It counts against the API-key bucket, separate from any user bucket.
    key_id = await env.admin.fetchval("SELECT id FROM public.api_keys WHERE org_id = $1", org_id)
    assert await env.redis.get(f"rl:k:{key_id}") is not None
    # last_used_at was stamped on verification.
    last_used = await env.admin.fetchval(
        "SELECT last_used_at FROM public.api_keys WHERE id = $1", key_id
    )
    assert last_used is not None


# -- gate 5: comparison mode returns a table with >=1 honest "insufficient" cell ----


async def test_comparison_mode_returns_table_with_insufficient_cell(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner()
    response = await env.client.post(
        "/api/v1/queries",
        json={
            "query": "Compare warfarin and xyzzyplacebonium for stroke prevention",
            "mode": "comparison",
            "entities": ["warfarin", "xyzzyplacebonium"],
        },
        headers=env.auth(token),
    )
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    result = next(e for e in events if e["stage"] == "result")
    table = result["data"]["comparison_table"]

    assert table["entities"] == ["warfarin", "xyzzyplacebonium"]
    cells = table["cells"]
    assert len(cells) == len(table["entities"]) * len(table["outcomes"])
    insufficient = [c for c in cells if not c["sufficient"]]
    assert insufficient, "comparison must honestly mark cells with no evidence"
    assert all(c["summary"] == "Insufficient evidence." for c in insufficient)


# -- gate 6: exceeding the rate limit returns 429 + Retry-After ---------------------


async def test_rate_limit_returns_429_with_retry_after(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner(plan="free")
    # Free plan: 20 requests/user/min. Use the cheap history endpoint.
    last = None
    for _ in range(25):
        last = await env.client.get("/api/v1/queries?limit=1", headers=env.auth(token))
        if last.status_code == 429:
            break
    assert last is not None
    assert last.status_code == 429, "the user bucket must eventually trip"
    assert last.json()["error"]["code"] == "rate_limited"
    assert int(last.headers["retry-after"]) >= 1


# -- gate 7: real cost is recorded on the answer row --------------------------------


async def test_cost_is_recorded_on_the_answer_row(env: ApiEnv) -> None:
    org_id, user_id, _token = await env.new_org_with_owner()
    service = AskService(env.pool, env.redis)
    events = [e async for e in service.ask(query=GROUNDED_QUERY, org_id=org_id, user_id=user_id)]
    query_id = UUID(events[0]["data"]["query_id"])

    row = await env.admin.fetchrow(
        "SELECT input_tokens, output_tokens, cost_usd, cached FROM public.answers "
        "WHERE query_id = $1",
        query_id,
    )
    assert row is not None
    # Offline heuristic backend does no paid work → truthful zero, not a guess.
    assert row["cost_usd"] == 0
    assert row["input_tokens"] == 0
    assert row["output_tokens"] == 0
    assert row["cached"] is False
