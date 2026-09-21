"""Phase 12: the feedback → golden-set loop and the ablation switches on the
real database (migration 020, the review-queue endpoint, promote/reject)."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from evals.golden.promote import fetch_review, list_reviews, promote, reject
from evals.golden.schema import load_set
from tests.conftest import ApiEnv


async def _seed_answer(env: ApiEnv, org_id: UUID, user_id: UUID, *, chunk_id: UUID) -> UUID:
    session_id, query_id, answer_id = uuid4(), uuid4(), uuid4()
    await env.admin.execute(
        "INSERT INTO public.query_sessions (id, org_id, user_id) VALUES ($1, $2, $3)",
        session_id,
        org_id,
        user_id,
    )
    await env.admin.execute(
        "INSERT INTO public.queries (id, session_id, org_id, user_id, raw_query, status) "
        "VALUES ($1, $2, $3, $4, 'Is apixaban safe in stage 4 CKD?', 'completed')",
        query_id,
        session_id,
        org_id,
        user_id,
    )
    citations = json.dumps([{"marker": 1, "chunk_id": str(chunk_id), "passage": "p"}])
    await env.admin.execute(
        "INSERT INTO public.answers (id, query_id, org_id, content, citations, confidence, "
        "model, prompt_version) VALUES ($1, $2, $3, 'Apixaban is contraindicated [1].', "
        "$4::jsonb, 'high', 'test-model', 'test.v1')",
        answer_id,
        query_id,
        org_id,
        citations,
    )
    return answer_id


async def test_thumbs_down_is_queued_and_promotion_grows_the_set(
    env: ApiEnv, tmp_path: Path
) -> None:
    org_id, _owner_id, owner_token = await env.new_org_with_owner()
    member_id, member_token = await env.add_member(org_id, "clinician")
    chunk_id = await env.admin.fetchval(
        "SELECT id FROM public.chunks WHERE org_id IS NULL ORDER BY id LIMIT 1"
    )
    if chunk_id is None:
        pytest.skip("no shared corpus in this database")
    answer_id = await _seed_answer(env, org_id, member_id, chunk_id=chunk_id)

    # A thumbs-up and a thumbs-down for a benign reason do not queue anything.
    ok = await env.client.post(
        "/api/v1/feedback",
        json={"answer_id": str(answer_id), "rating": "up"},
        headers=env.auth(owner_token),
    )
    assert ok.status_code == 201
    incomplete = await env.client.post(
        "/api/v1/feedback",
        json={"answer_id": str(answer_id), "rating": "down", "reason": "incomplete"},
        headers=env.auth(member_token),
    )
    assert incomplete.status_code == 201
    queue = await env.client.get("/api/v1/feedback/review-queue", headers=env.auth(owner_token))
    assert queue.status_code == 200 and queue.json()["pending"] == 0

    # The member changes their mind: the answer was wrong. Now it is queued.
    wrong = await env.client.post(
        "/api/v1/feedback",
        json={"answer_id": str(answer_id), "rating": "down", "reason": "wrong"},
        headers=env.auth(member_token),
    )
    assert wrong.status_code == 201
    queue = await env.client.get("/api/v1/feedback/review-queue", headers=env.auth(owner_token))
    body = queue.json()
    assert body["pending"] == 1 and len(body["items"]) == 1
    entry = body["items"][0]
    assert entry["question"] == "Is apixaban safe in stage 4 CKD?" and entry["reason"] == "wrong"
    assert entry["citations"] == 1 and entry["status"] == "pending"
    # Members cannot see the queue; owners of another org see nothing.
    forbidden = await env.client.get(
        "/api/v1/feedback/review-queue", headers=env.auth(member_token)
    )
    assert forbidden.status_code == 403
    _other_org, _other_owner, other_token = await env.new_org_with_owner()
    other = await env.client.get("/api/v1/feedback/review-queue", headers=env.auth(other_token))
    assert other.json()["pending"] == 0

    # Review: promote it with a reviewer-written reference answer.
    review_id = UUID(entry["id"])
    pending = await list_reviews(env.pool, "pending")
    assert any(str(r["id"]) == entry["id"] for r in pending)
    set_path = tmp_path / "set.jsonl"
    set_path.write_text("", encoding="utf-8")
    item = await promote(
        env.pool,
        review_id,
        reviewer="Dr Reviewer",
        category="therapy",
        reference_answer=(
            "Apixaban is not contraindicated in stage 4 CKD; guidelines permit it with dose "
            "adjustment, and observational data suggest less bleeding than warfarin."
        ),
        grade="B",
        chunk_ids=None,
        abstain=False,
        contradiction=False,
        note="answer inverted the guideline",
        set_path=set_path,
    )
    assert item.id == "feedback-001" and item.relevant_chunk_ids == [str(chunk_id)]
    assert item.provenance.source == "feedback" and item.provenance.reviewed_by == "Dr Reviewer"
    assert [i.id for i in load_set(set_path)] == ["feedback-001"]
    reviewed = await fetch_review(env.pool, review_id)
    assert reviewed is not None and reviewed["status"] == "promoted"
    assert reviewed["golden_id"] == "feedback-001"
    with pytest.raises(SystemExit):  # promoting twice is refused
        await promote(
            env.pool,
            review_id,
            reviewer="x",
            category="therapy",
            reference_answer="y" * 50,
            grade=None,
            chunk_ids=None,
            abstain=False,
            contradiction=False,
            note=None,
            set_path=set_path,
        )
    queue = await env.client.get(
        "/api/v1/feedback/review-queue?status=promoted", headers=env.auth(owner_token)
    )
    assert queue.json()["items"][0]["golden_id"] == "feedback-001"

    # Reject path on a second case.
    second = await _seed_answer(env, org_id, member_id, chunk_id=chunk_id)
    await env.client.post(
        "/api/v1/feedback",
        json={"answer_id": str(second), "rating": "down", "reason": "unsupported"},
        headers=env.auth(member_token),
    )
    second_review = await env.admin.fetchval(
        "SELECT id FROM public.golden_reviews WHERE answer_id = $1", second
    )
    await reject(env.pool, second_review, reviewer="Dr Reviewer", note="question was ambiguous")
    with pytest.raises(SystemExit):
        await reject(env.pool, second_review, reviewer="x", note="again")
    queue = await env.client.get("/api/v1/feedback/review-queue", headers=env.auth(owner_token))
    assert (queue.json()["promoted"], queue.json()["rejected"]) == (1, 1)
