"""Phase 13: the cost ledger. Every provider call a query makes is written to
``cost_events`` with its tenant and query, priced at the published rate; a
local stand-in is a truthful $0 with a labelled projection; what the caches
avoided is recorded, not estimated."""

from __future__ import annotations

from typing import Any

import pytest

from app.services import cost
from app.services.ask import AskService
from tests.conftest import ApiEnv

GROUNDED_QUERY = "How is type 2 diabetes managed with metformin?"


def test_recording_outside_a_request_is_a_no_op() -> None:
    assert (
        cost.record("rerank", provider="local", model="bm25-local", units=1, unit="searches")
        is None
    )


def test_local_providers_cost_zero_and_project_to_cloud_rates() -> None:
    with cost.collecting() as collector:
        cost.record(
            "embedding", provider="local", model="hashing-lexical", units=1000, unit="tokens"
        )
        cost.record("rerank", provider="local", model="bm25-local", units=1, unit="searches")
        cost.record(
            "generation",
            provider="local",
            model="heuristic",
            units=2000,
            unit="tokens",
            output_units=0,
        )
        cost.record("stt", provider="local", model="tiny.en", units=12.5, unit="seconds")
        cost.record("tts", provider="local", model="local:Zira", units=400, unit="characters")
    summary = cost.summary(collector)
    assert summary["cost_usd"] == 0.0, "the offline backend genuinely costs nothing"
    expected = (
        cost.priced("embedding", "embed-v4.0", 1000)
        + cost.priced("rerank", "rerank-v3.5", 1)
        + cost.priced("generation", "claude-sonnet-4-6", 2000)
        + cost.priced("stt", "nova-3-medical", 12.5)
        + cost.priced("tts", "eleven_flash_v2_5", 400)
    )
    assert summary["projected_usd"] == round(expected, 6)
    assert summary["projected_usd"] > 0
    # Outside the context nothing is recorded anymore.
    assert cost.current() is None


def test_cloud_generation_is_priced_per_direction_and_split_into_rows() -> None:
    with cost.collecting() as collector:
        event = cost.record(
            "generation",
            provider="anthropic",
            model="claude-sonnet-4-6",
            units=1_000_000,
            unit="tokens",
            output_units=100_000,
        )
    assert event is not None
    assert event.cost_usd == round(3.0 + 1.5, 6)
    rows = cost._rows(collector)
    assert [(r[4], r[3], r[5]) for r in rows] == [
        ("tokens", 1_000_000.0, 3.0),
        ("output_tokens", 100_000.0, 1.5),
    ]
    assert cost.projected_usd(event) == event.cost_usd, "a cloud call projects to itself"


def test_snapshot_replays_as_avoided_units() -> None:
    with cost.collecting() as original:
        cost.record("embedding", provider="local", model="hashing-lexical", units=10, unit="tokens")
        cost.record("rerank", provider="local", model="bm25-local", units=1, unit="searches")
        cost.record("generation", provider="local", model="heuristic", units=500, unit="tokens")
    snapshot = original.snapshot()
    assert [s["component"] for s in snapshot] == ["rerank", "generation"], (
        "a hit still embeds the query, so the embedding is never counted as avoided"
    )
    with cost.collecting() as hit:
        assert cost.replay_cached(snapshot) == 2
    assert all(e.cached for e in hit.events)
    assert cost.summary(hit)["saved_usd"] == 0.0
    assert cost.summary(hit)["saved_projected_usd"] == round(
        cost.priced("rerank", "rerank-v3.5", 1)
        + cost.priced("generation", "claude-sonnet-4-6", 500),
        6,
    )


async def test_a_query_writes_its_provider_calls_to_the_ledger(env: ApiEnv) -> None:
    org_id, user_id, token = await env.new_org_with_owner()
    service = AskService(env.pool, env.redis)
    first = [e async for e in service.ask(query=GROUNDED_QUERY, org_id=org_id, user_id=user_id)]
    query_id = first[0]["data"]["query_id"]
    rows = await env.admin.fetch(
        "SELECT component::text AS component, provider, model, units, unit, cost_usd, cached "
        "FROM public.cost_events WHERE org_id = $1 AND query_id = $2 ORDER BY created_at",
        org_id,
        query_id,
    )
    billed_components = {r["component"] for r in rows if not r["cached"]}
    assert billed_components >= {"rerank", "generation"}, rows
    # The query embedding is billed, or — when the embedding cache already
    # held this text from an earlier run — recorded as avoided units.
    assert {r["component"] for r in rows} >= {"embedding", "rerank", "generation"}, rows
    assert all(float(r["units"]) > 0 for r in rows)
    assert all(r["provider"] == "local" and float(r["cost_usd"]) == 0 for r in rows), (
        "offline backend: real units, truthful $0"
    )
    rerank = next(r for r in rows if r["component"] == "rerank")
    assert rerank["model"] == "bm25-local" and rerank["unit"] == "searches"

    # The same question again is a semantic-cache hit: what the stored answer
    # took (rerank + generation) is recorded as avoided, not re-billed.
    second = [e async for e in service.ask(query=GROUNDED_QUERY, org_id=org_id, user_id=user_id)]
    assert second[-1]["data"]["cached"] is True
    hit_rows = await env.admin.fetch(
        "SELECT component::text AS component, cached FROM public.cost_events "
        "WHERE org_id = $1 AND query_id = $2",
        org_id,
        second[0]["data"]["query_id"],
    )
    avoided = {r["component"] for r in hit_rows if r["cached"]}
    billed = {r["component"] for r in hit_rows if not r["cached"]}
    assert avoided >= {"rerank", "generation"}, hit_rows
    assert billed <= {"embedding"}, "a hit still embeds the query and nothing else"
    assert not any(r["component"] in ("rerank", "generation") and not r["cached"] for r in hit_rows)

    # The dashboard reads the same rows: per-component spend, the projection
    # labelled as such, and the savings split by cache.
    response = await env.client.get("/api/v1/analytics/cost?days=7", headers=env.auth(token))
    assert response.status_code == 200, response.text
    report: dict[str, Any] = response.json()
    assert report["offline"] is True
    assert report["total_cost_usd"] == 0.0
    assert report["total_projected_usd"] > 0
    assert report["by_component"]["generation"] == 0.0
    assert report["by_component_projected"]["generation"] > 0
    assert report["semantic_cache_saved_usd"] == 0.0
    assert report["semantic_cache_saved_projected_usd"] > 0
    assert report["month_semantic_cache_saved_projected_usd"] > 0
    lines = {(line["component"], line["model"]): line for line in report["lines"]}
    # Two searches per question — its own words, then its topic words alone
    # (graph.retrieve) — so the cache hit avoided both.
    assert lines[("rerank", "bm25-local")]["cached_units"] == 2.0
    assert lines[("rerank", "bm25-local")]["calls"] >= 1
    assert len(report["series"]) == 7
    assert sum(p["saved_projected_usd"] for p in report["series"]) == pytest.approx(
        report["semantic_cache_saved_projected_usd"]
        + report["embedding_cache_saved_projected_usd"],
        abs=1e-5,
    )
