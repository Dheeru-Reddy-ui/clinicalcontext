"""Phase 9 Tier-2 surface: sessions, documents, feedback, analytics, suggest,
corpus freshness — exercised against the real app, Postgres and Redis.

Uses the shared ``env`` harness from conftest (real JWTs through the
production verification path, real seeded corpus, offline AI backend).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from tests.conftest import ApiEnv, parse_sse

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "")

pytestmark = pytest.mark.skipif(
    not (TEST_DATABASE_URL and TEST_REDIS_URL),
    reason="TEST_DATABASE_URL/TEST_REDIS_URL not set (needs a running Postgres + Redis)",
)

GROUNDED_QUERY = "How is type 2 diabetes managed with metformin?"


async def _ask(env: ApiEnv, token: str, query: str, **body: object) -> dict[str, object]:
    """Run a query through the streaming endpoint; return the result event data."""
    response = await env.client.post(
        "/api/v1/queries", json={"query": query, **body}, headers=env.auth(token)
    )
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    result = next((e for e in events if e["stage"] == "result"), None)
    assert result is not None, f"no result event; stages={[e['stage'] for e in events]}"
    data: dict[str, object] = result["data"]
    return data


# -- sessions (multi-turn) ------------------------------------------------------------


async def test_session_thread_records_turns_and_contextualizes_followup(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner()

    created = await env.client.post(
        "/api/v1/sessions", json={"title": "Diabetes rounds"}, headers=env.auth(token)
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]

    await _ask(env, token, GROUNDED_QUERY, session_id=session_id)
    # A bare anaphoric follow-up only makes sense against the previous turn.
    await _ask(env, token, "what about in pregnancy?", session_id=session_id)

    detail = await env.client.get(f"/api/v1/sessions/{session_id}", headers=env.auth(token))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["session"]["query_count"] == 2
    turns = body["turns"]
    assert len(turns) == 2
    assert turns[0]["raw_query"] == GROUNDED_QUERY
    # The follow-up was resolved against the first turn before retrieval.
    assert turns[1]["contextualized_query"] is not None
    assert "metformin" in turns[1]["contextualized_query"].lower()
    assert "pregnancy" in turns[1]["contextualized_query"].lower()

    listed = await env.client.get("/api/v1/sessions", headers=env.auth(token))
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


# -- documents browse -----------------------------------------------------------------


async def test_documents_browse_exposes_shared_corpus_with_chunks(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner()

    listing = await env.client.get(
        "/api/v1/documents?limit=5&scope=shared", headers=env.auth(token)
    )
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["total"] > 0, "the seeded shared corpus should be visible"
    document = body["documents"][0]
    assert document["shared"] is True
    assert document["chunk_count"] >= 1

    detail = await env.client.get(f"/api/v1/documents/{document['id']}", headers=env.auth(token))
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == document["id"]

    chunks = await env.client.get(
        f"/api/v1/documents/{document['id']}/chunks?limit=5", headers=env.auth(token)
    )
    assert chunks.status_code == 200, chunks.text
    chunk_body = chunks.json()
    assert chunk_body["total"] == document["chunk_count"]
    indexes = [c["chunk_index"] for c in chunk_body["chunks"]]
    assert indexes == sorted(indexes), "chunks must come back in document order"


def _guideline_pdf(path: Path, *, title: str) -> Path:
    """A small but real PDF, so the upload path parses actual PDF bytes."""
    page = canvas.Canvas(str(path), pagesize=letter)
    page.setFont("Helvetica-Bold", 20)
    page.drawString(72, 730, title)
    page.setFont("Helvetica", 11)
    page.drawString(72, 700, "Internal protocol produced by the Test Society of Cardiology.")
    page.setFont("Helvetica-Bold", 15)
    page.drawString(72, 660, "1. Recommendations")
    page.setFont("Helvetica", 11)
    page.drawString(72, 640, "Patients with stage 2 hypertension should receive therapy.")
    page.drawString(72, 626, "Lifestyle modification remains foundational for all groups.")
    page.showPage()
    page.save()
    return path


async def test_private_upload_is_ingested_and_isolated(env: ApiEnv, tmp_path: Path) -> None:
    org_id, _user_id, token = await env.new_org_with_owner()
    _org_b, _user_b, token_b = await env.new_org_with_owner()
    title = f"Local HTN Protocol {uuid4().hex[:8]}"
    pdf = _guideline_pdf(tmp_path / "protocol.pdf", title=title)

    with pdf.open("rb") as handle:
        response = await env.client.post(
            "/api/v1/documents/upload",
            files={"file": ("protocol.pdf", handle, "application/pdf")},
            data={"title": title},
            headers=env.auth(token),
        )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["deduplicated"] is False
    assert body["chunk_count"] >= 1
    # Offline embedder, so it is searchable immediately rather than deferred.
    assert body["embedded_count"] == body["chunk_count"]
    document_id = body["document_id"]

    # It lands in THIS org's private corpus...
    stored_org = await env.admin.fetchval(
        "SELECT org_id FROM public.documents WHERE id = $1", document_id
    )
    assert stored_org == org_id, "an upload must be pinned to the uploader's org"

    private = await env.client.get("/api/v1/documents?scope=private", headers=env.auth(token))
    assert private.json()["total"] == 1
    assert private.json()["documents"][0]["id"] == document_id

    # ...and is invisible to another tenant.
    assert (
        await env.client.get(f"/api/v1/documents/{document_id}", headers=env.auth(token_b))
    ).status_code == 404
    other = await env.client.get("/api/v1/documents?scope=private", headers=env.auth(token_b))
    assert other.json()["total"] == 0


async def test_the_same_pdf_can_live_in_two_tenants(env: ApiEnv, tmp_path: Path) -> None:
    """De-duplication is per tenant (migration 023).

    It used to be global, so uploading a PDF another organisation already held
    returned "this document already exists in another tenant's corpus" — one
    tenant could test for the existence of another's private document. Two
    hospitals holding the same guideline is also simply the right behaviour.
    """
    org_a, _ua, token_a = await env.new_org_with_owner()
    org_b, _ub, token_b = await env.new_org_with_owner()
    title = f"Shared Guideline {uuid4().hex[:8]}"
    pdf = _guideline_pdf(tmp_path / "shared.pdf", title=title)

    ids = []
    for token in (token_a, token_b):
        with pdf.open("rb") as handle:
            response = await env.client.post(
                "/api/v1/documents/upload",
                files={"file": ("shared.pdf", handle, "application/pdf")},
                data={"title": title},
                headers=env.auth(token),
            )
        assert response.status_code == 201, response.text
        assert response.json()["deduplicated"] is False
        ids.append(response.json()["document_id"])

    assert ids[0] != ids[1], "each tenant owns its own copy"
    owners = await env.admin.fetch(
        "SELECT id, org_id FROM public.documents WHERE id = ANY($1::uuid[])",
        [UUID(i) for i in ids],
    )
    assert {r["org_id"] for r in owners} == {org_a, org_b}

    # Re-uploading inside one tenant still de-duplicates to that tenant's copy.
    with pdf.open("rb") as handle:
        again = await env.client.post(
            "/api/v1/documents/upload",
            files={"file": ("shared.pdf", handle, "application/pdf")},
            data={"title": title},
            headers=env.auth(token_a),
        )
    assert again.status_code == 201, again.text
    assert again.json()["deduplicated"] is True
    assert again.json()["document_id"] == ids[0]


async def test_upload_rejects_non_pdf(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner()
    response = await env.client.post(
        "/api/v1/documents/upload",
        files={"file": ("notes.txt", b"just some text", "text/plain")},
        headers=env.auth(token),
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_upload"


async def test_viewer_cannot_upload(env: ApiEnv, tmp_path: Path) -> None:
    org_id, _owner_id, _owner_token = await env.new_org_with_owner()
    _viewer_id, viewer_token = await env.add_member(org_id, "viewer")
    pdf = _guideline_pdf(tmp_path / "viewer.pdf", title="Viewer Upload Attempt")

    with pdf.open("rb") as handle:
        response = await env.client.post(
            "/api/v1/documents/upload",
            files={"file": ("viewer.pdf", handle, "application/pdf")},
            headers=env.auth(viewer_token),
        )
    assert response.status_code == 403


async def test_new_org_sees_no_private_documents(env: ApiEnv) -> None:
    """Tenant isolation: a fresh org's private corpus is empty, not the shared one."""
    _org_id, _user_id, token = await env.new_org_with_owner()
    response = await env.client.get("/api/v1/documents?scope=private", headers=env.auth(token))
    assert response.status_code == 200
    assert response.json()["total"] == 0


async def test_document_from_another_tenant_is_not_found(env: ApiEnv) -> None:
    """A private upload is invisible to a second org — 404, not 403."""
    org_a, user_a, _token_a = await env.new_org_with_owner()
    _org_b, _user_b, token_b = await env.new_org_with_owner()
    document_id = await env.admin.fetchval(
        """
        INSERT INTO public.documents (org_id, source_type, title, content_hash)
        VALUES ($1, 'uploaded', 'Org A private protocol', $2) RETURNING id
        """,
        org_a,
        f"hash-{user_a.hex}",
    )
    response = await env.client.get(f"/api/v1/documents/{document_id}", headers=env.auth(token_b))
    assert response.status_code == 404


# -- feedback -------------------------------------------------------------------------


async def test_feedback_is_one_vote_per_user_and_updatable(env: ApiEnv) -> None:
    org_id, _user_id, token = await env.new_org_with_owner()
    answer_id = (await _ask(env, token, GROUNDED_QUERY))["answer_id"]

    first = await env.client.post(
        "/api/v1/feedback",
        json={
            "answer_id": answer_id,
            "rating": "down",
            "reason": "should_have_abstained",
            "comment": "thin evidence",
        },
        headers=env.auth(token),
    )
    assert first.status_code == 201, first.text
    assert first.json()["reason"] == "should_have_abstained"

    # Re-voting updates the existing row rather than stacking duplicates.
    second = await env.client.post(
        "/api/v1/feedback",
        json={"answer_id": answer_id, "rating": "up"},
        headers=env.auth(token),
    )
    assert second.status_code == 201, second.text
    assert second.json()["rating"] == "up"
    assert second.json()["reason"] is None

    count = await env.admin.fetchval(
        "SELECT count(*) FROM public.feedback WHERE org_id = $1", org_id
    )
    assert count == 1


async def test_feedback_on_unknown_answer_is_404(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner()
    response = await env.client.post(
        "/api/v1/feedback",
        json={"answer_id": "00000000-0000-0000-0000-000000000000", "rating": "up"},
        headers=env.auth(token),
    )
    assert response.status_code == 404


# -- analytics ------------------------------------------------------------------------


async def test_analytics_reflect_real_activity(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner()
    answer_id = (await _ask(env, token, GROUNDED_QUERY))["answer_id"]
    await env.client.post(
        "/api/v1/feedback",
        json={"answer_id": answer_id, "rating": "down", "reason": "incomplete"},
        headers=env.auth(token),
    )

    overview = await env.client.get("/api/v1/analytics/overview", headers=env.auth(token))
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["total_queries"] == 1
    assert body["answered"] + body["abstained"] == 1
    # Offline backend does no paid work, so the truthful total is exactly zero.
    assert body["total_cost_usd"] == 0.0
    assert body["latency_p50_ms"] is not None

    usage = await env.client.get("/api/v1/analytics/usage?days=7", headers=env.auth(token))
    assert usage.status_code == 200
    points = usage.json()["points"]
    assert len(points) == 7, "days are zero-filled for charting"
    assert sum(p["queries"] for p in points) == 1

    quality = await env.client.get("/api/v1/analytics/quality", headers=env.auth(token))
    assert quality.status_code == 200, quality.text
    quality_body = quality.json()
    assert quality_body["feedback_down"] == 1
    assert quality_body["feedback_by_reason"]["incomplete"] == 1
    assert quality_body["top_queries"][0]["query"] == GROUNDED_QUERY


async def test_analytics_are_tenant_scoped(env: ApiEnv) -> None:
    _org_a, _user_a, token_a = await env.new_org_with_owner()
    _org_b, _user_b, token_b = await env.new_org_with_owner()
    await _ask(env, token_a, GROUNDED_QUERY)

    other = await env.client.get("/api/v1/analytics/overview", headers=env.auth(token_b))
    assert other.status_code == 200
    assert other.json()["total_queries"] == 0, "one org must not see another's volume"


async def test_rates_are_null_not_zero_without_data(env: ApiEnv) -> None:
    """An empty org has no abstention rate — reporting 0.0 would be a lie."""
    _org_id, _user_id, token = await env.new_org_with_owner()
    response = await env.client.get("/api/v1/analytics/overview", headers=env.auth(token))
    assert response.status_code == 200
    assert response.json()["abstention_rate"] is None


# -- PICO -----------------------------------------------------------------------------


async def test_pico_is_stored_and_seeds_decomposition(env: ApiEnv) -> None:
    """Structured PICO drives the plan: it is persisted and it fans the query
    out into intervention / comparison / head-to-head sub-questions."""
    _org_id, _user_id, token = await env.new_org_with_owner()
    pico = {
        "population": "adults with type 2 diabetes",
        "intervention": "metformin",
        "comparison": "sulfonylurea",
        "outcome": "cardiovascular mortality",
    }
    result = await _ask(env, token, "Which agent is preferred?", pico=pico)

    assert result["is_multi_hop"] is True, "structured PICO always implies a decomposed plan"
    sub_questions = result["sub_questions"]
    assert any("metformin" in s for s in sub_questions)
    assert any("sulfonylurea" in s for s in sub_questions)
    assert any("versus" in s for s in sub_questions), "a head-to-head sub-question is expected"
    assert all("cardiovascular mortality" in s for s in sub_questions)

    stored = await env.admin.fetchval(
        "SELECT pico FROM public.queries WHERE id = $1", UUID(str(result["query_id"]))
    )
    assert (stored if isinstance(stored, dict) else json.loads(stored)) == pico


# -- autocomplete ---------------------------------------------------------------------


async def test_suggest_returns_mesh_vocabulary(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner()
    response = await env.client.get("/api/v1/suggest?q=atrial", headers=env.auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    values = [s["value"] for s in body["suggestions"]]
    assert "atrial fibrillation" in values
    assert all(s["kind"] == "mesh" for s in body["suggestions"])
    # Check tags ("humans", "female") are excluded from the vocabulary entirely.
    assert "humans" not in values


async def test_suggest_surfaces_this_orgs_history_first(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner()
    await _ask(env, token, GROUNDED_QUERY)

    response = await env.client.get("/api/v1/suggest?q=metformin", headers=env.auth(token))
    assert response.status_code == 200
    suggestions = response.json()["suggestions"]
    assert suggestions[0]["kind"] == "history"
    assert suggestions[0]["value"] == GROUNDED_QUERY


async def test_suggest_history_does_not_leak_across_tenants(env: ApiEnv) -> None:
    _org_a, _user_a, token_a = await env.new_org_with_owner()
    _org_b, _user_b, token_b = await env.new_org_with_owner()
    await _ask(env, token_a, GROUNDED_QUERY)

    response = await env.client.get("/api/v1/suggest?q=metformin", headers=env.auth(token_b))
    assert response.status_code == 200
    assert not [s for s in response.json()["suggestions"] if s["kind"] == "history"]


async def test_suggest_ignores_too_short_a_prefix(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner()
    response = await env.client.get("/api/v1/suggest?q=a", headers=env.auth(token))
    assert response.status_code == 200
    assert response.json()["suggestions"] == []


# -- corpus freshness -----------------------------------------------------------------


async def test_corpus_freshness_reports_unknown_until_the_job_runs(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner()
    response = await env.client.get("/api/v1/corpus/freshness", headers=env.auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["corpus"]["documents"] > 0
    assert body["corpus"]["newest_publication"] is not None
    if not body["domains"]:
        # The weekly re-query has never run here: say so, don't imply freshness.
        assert body["last_checked_at"] is None
        assert body["stale_domains"] == []


# -- read-only principals -------------------------------------------------------------


async def test_viewer_cannot_run_queries_but_can_browse(env: ApiEnv) -> None:
    org_id, _owner_id, _owner_token = await env.new_org_with_owner()
    _viewer_id, viewer_token = await env.add_member(org_id, "viewer")

    blocked = await env.client.post(
        "/api/v1/queries", json={"query": GROUNDED_QUERY}, headers=env.auth(viewer_token)
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "permission_denied"

    allowed = await env.client.get("/api/v1/documents?limit=1", headers=env.auth(viewer_token))
    assert allowed.status_code == 200
