"""Phase 9 Tier-3: batch jobs, webhooks, and Living Answers.

Webhook delivery is driven through an httpx MockTransport rather than a real
server, so the production signing/retry/backoff code runs unchanged while the
receiver stays deterministic.
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest

from app.services.living_answers import (
    SIMILARITY_FLOOR,
    diff_answers,
    refresh_followed_answers,
)
from app.services.webhooks import (
    MAX_ATTEMPTS,
    SIGNATURE_HEADER,
    deliver_pending,
    verify_signature,
)
from tests.conftest import ApiEnv, parse_sse

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "")

pytestmark = pytest.mark.skipif(
    not (TEST_DATABASE_URL and TEST_REDIS_URL),
    reason="TEST_DATABASE_URL/TEST_REDIS_URL not set (needs a running Postgres + Redis)",
)

GROUNDED_QUERY = "How is type 2 diabetes managed with metformin?"
HOOK_URL = "https://hooks.example.test/clinicalcontext"


async def _ask(env: ApiEnv, token: str, query: str) -> dict[str, object]:
    response = await env.client.post(
        "/api/v1/queries", json={"query": query}, headers=env.auth(token)
    )
    assert response.status_code == 200, response.text
    result = next(e for e in parse_sse(response.text) if e["stage"] == "result")
    data: dict[str, object] = result["data"]
    return data


async def _register_hook(env: ApiEnv, token: str, events: list[str]) -> tuple[str, str]:
    response = await env.client.post(
        "/api/v1/webhooks", json={"url": HOOK_URL, "events": events}, headers=env.auth(token)
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["id"], body["secret"]


def _recording_client(status_code: int = 200) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status_code, text="" if status_code < 300 else "receiver exploded")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), captured


# -- batch jobs -----------------------------------------------------------------------


async def test_batch_job_answers_every_query_and_reports_progress(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner()
    queries = [
        GROUNDED_QUERY,
        "What is the evidence for anticoagulation in atrial fibrillation?",
        "What is the role of statins in cardiovascular disease prevention?",
    ]
    submitted = await env.client.post(
        "/api/v1/batches", json={"queries": queries}, headers=env.auth(token)
    )
    assert submitted.status_code == 202, submitted.text
    body = submitted.json()
    assert body["total"] == 3
    batch_id = body["batch_id"]

    for _ in range(120):
        polled = await env.client.get(f"/api/v1/batches/{batch_id}", headers=env.auth(token))
        assert polled.status_code == 200, polled.text
        state = polled.json()
        if state["status"] == "completed":
            break
        await asyncio.sleep(0.25)
    else:
        pytest.fail(f"batch did not finish: {state}")

    assert state["completed"] == 3
    assert state["failed"] == 0
    assert [i["position"] for i in state["items"]] == [0, 1, 2]
    for item, query in zip(state["items"], queries, strict=True):
        assert item["query"] == query
        assert item["status"] == "completed"
        assert item["answer_id"], "every completed item must carry its answer"
        assert item["answer"]


async def test_batch_rejects_more_than_fifty_queries(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner()
    response = await env.client.post(
        "/api/v1/batches", json={"queries": ["q"] * 51}, headers=env.auth(token)
    )
    assert response.status_code == 422


async def test_batch_is_not_visible_to_another_tenant(env: ApiEnv) -> None:
    _org_a, _user_a, token_a = await env.new_org_with_owner()
    _org_b, _user_b, token_b = await env.new_org_with_owner()
    submitted = await env.client.post(
        "/api/v1/batches", json={"queries": [GROUNDED_QUERY]}, headers=env.auth(token_a)
    )
    batch_id = submitted.json()["batch_id"]

    response = await env.client.get(f"/api/v1/batches/{batch_id}", headers=env.auth(token_b))
    assert response.status_code == 404


# -- webhooks -------------------------------------------------------------------------


async def test_query_completion_queues_a_signed_delivery(env: ApiEnv) -> None:
    org_id, _user_id, token = await env.new_org_with_owner()
    _hook_id, secret = await _register_hook(env, token, ["query.completed"])

    answer = await _ask(env, token, GROUNDED_QUERY)

    queued = await env.admin.fetch(
        "SELECT event, status, payload FROM public.webhook_deliveries WHERE org_id = $1", org_id
    )
    assert len(queued) == 1, "a registered webhook must receive the completion event"
    assert queued[0]["event"] == "query.completed"
    assert queued[0]["status"] == "pending"

    client, captured = _recording_client()
    try:
        tally = await deliver_pending(env.pool, client=client)
    finally:
        await client.aclose()
    assert tally["delivered"] >= 1

    assert len(captured) == 1
    request = captured[0]
    assert str(request.url) == HOOK_URL
    body = request.content.decode()
    # The receiver can verify the payload really came from us, unmodified.
    assert verify_signature(secret, body, request.headers[SIGNATURE_HEADER])
    assert not verify_signature("whsec_wrong", body, request.headers[SIGNATURE_HEADER])
    envelope = json.loads(body)
    assert envelope["event"] == "query.completed"
    assert envelope["data"]["answer_id"] == answer["answer_id"]

    delivered = await env.admin.fetchrow(
        "SELECT status, attempts, response_code, delivered_at FROM public.webhook_deliveries "
        "WHERE org_id = $1",
        org_id,
    )
    assert delivered["status"] == "delivered"
    assert delivered["attempts"] == 1
    assert delivered["response_code"] == 200
    assert delivered["delivered_at"] is not None


async def test_failed_delivery_is_retried_with_backoff_then_given_up_on(env: ApiEnv) -> None:
    org_id, _user_id, token = await env.new_org_with_owner()
    await _register_hook(env, token, ["query.completed"])
    await _ask(env, token, GROUNDED_QUERY)

    client, _ = _recording_client(status_code=500)
    try:
        tally = await deliver_pending(env.pool, client=client)
        assert tally["retried"] >= 1

        row = await env.admin.fetchrow(
            "SELECT status, attempts, response_code, last_error, next_attempt_at "
            "FROM public.webhook_deliveries WHERE org_id = $1",
            org_id,
        )
        assert row["status"] == "pending", "a failure schedules a retry, it does not drop"
        assert row["attempts"] == 1
        assert row["response_code"] == 500
        assert "exploded" in row["last_error"]
        assert row["next_attempt_at"] is not None

        # Drive it to exhaustion: each pass is due immediately once we clear the backoff.
        for _ in range(MAX_ATTEMPTS):
            await env.admin.execute(
                "UPDATE public.webhook_deliveries SET next_attempt_at = now() "
                "WHERE org_id = $1 AND status = 'pending'",
                org_id,
            )
            await deliver_pending(env.pool, client=client)
    finally:
        await client.aclose()

    final = await env.admin.fetchrow(
        "SELECT status, attempts, next_attempt_at FROM public.webhook_deliveries WHERE org_id = $1",
        org_id,
    )
    assert final["status"] == "failed"
    assert final["attempts"] == MAX_ATTEMPTS
    # Given up on: nothing is scheduled, so it will not be retried forever.
    assert final["next_attempt_at"] is None


async def test_delivery_log_is_readable_and_secret_is_never_relisted(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner()
    _hook_id, secret = await _register_hook(env, token, ["query.completed"])
    await _ask(env, token, GROUNDED_QUERY)

    listed = await env.client.get("/api/v1/webhooks", headers=env.auth(token))
    assert listed.status_code == 200
    assert "secret" not in listed.json()["webhooks"][0]
    assert secret not in listed.text

    deliveries = await env.client.get("/api/v1/webhooks/deliveries", headers=env.auth(token))
    assert deliveries.status_code == 200, deliveries.text
    assert deliveries.json()["total"] == 1
    assert deliveries.json()["deliveries"][0]["event"] == "query.completed"


async def test_webhook_url_must_be_https(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner()
    response = await env.client.post(
        "/api/v1/webhooks",
        json={"url": "http://insecure.example.test/hook", "events": ["query.completed"]},
        headers=env.auth(token),
    )
    assert response.status_code == 422


async def test_a_cache_hit_still_fires_query_completed(env: ApiEnv) -> None:
    """Serving from cache is an implementation detail, not a reason to go quiet."""
    org_id, _user_id, token = await env.new_org_with_owner()
    await _register_hook(env, token, ["query.completed"])

    first = await _ask(env, token, GROUNDED_QUERY)
    second = await _ask(env, token, GROUNDED_QUERY)
    assert first["cached"] is False
    assert second["cached"] is True

    rows = await env.admin.fetch(
        "SELECT payload FROM public.webhook_deliveries "
        "WHERE org_id = $1 AND event = 'query.completed' ORDER BY created_at",
        org_id,
    )
    assert len(rows) == 2, "both the live answer and the cached one must notify"
    cached_payload = (
        json.loads(rows[1]["payload"])
        if isinstance(rows[1]["payload"], str)
        else rows[1]["payload"]
    )
    assert cached_payload["cached"] is True
    assert cached_payload["answer_id"] == second["answer_id"]


async def test_unsubscribed_events_are_not_delivered(env: ApiEnv) -> None:
    org_id, _user_id, token = await env.new_org_with_owner()
    await _register_hook(env, token, ["batch.completed"])  # not query.completed
    await _ask(env, token, GROUNDED_QUERY)

    count = await env.admin.fetchval(
        "SELECT count(*) FROM public.webhook_deliveries WHERE org_id = $1", org_id
    )
    assert count == 0


# -- Living Answers -------------------------------------------------------------------


def test_diff_is_material_only_when_something_clinically_changed() -> None:
    """Cosmetic rewording must not wake a clinician; new evidence must."""
    from app.schemas.answer import AnswerResult, Contradiction

    def result(answer: str, confidence: str = "moderate") -> AnswerResult:
        return AnswerResult(
            query="q",
            query_type="therapy",
            is_multi_hop=False,
            abstained=False,
            answer=answer,
            citations=[],
            contradiction=Contradiction(detected=False),
            confidence=confidence,  # type: ignore[arg-type]
            evidence_grade="B",
            retrieval_grade="sufficient",
        )

    unchanged = diff_answers(
        old_content="Metformin remains first line for type 2 diabetes.",
        old_citations=[],
        old_confidence="moderate",
        old_contradiction=False,
        result=result("Metformin remains first line for type 2 diabetes."),
    )
    assert unchanged.similarity == 1.0
    assert not unchanged.material

    moved = diff_answers(
        old_content="Metformin remains first line for type 2 diabetes.",
        old_citations=[],
        old_confidence="moderate",
        old_contradiction=False,
        result=result("Metformin remains first line for type 2 diabetes.", confidence="high"),
    )
    assert moved.material
    assert "confidence moved from moderate to high" in moved.reasons()

    rewritten = diff_answers(
        old_content="Metformin remains first line for type 2 diabetes.",
        old_citations=[],
        old_confidence="moderate",
        old_contradiction=False,
        result=result("SGLT2 inhibitors are now preferred for patients with cardiorenal risk."),
    )
    assert rewritten.similarity < SIMILARITY_FLOOR
    assert rewritten.material


async def test_following_an_answer_and_superseding_it_records_a_version(env: ApiEnv) -> None:
    org_id, user_id, token = await env.new_org_with_owner()
    await _register_hook(env, token, ["answer.superseded"])
    answer_id = (await _ask(env, token, GROUNDED_QUERY))["answer_id"]

    followed = await env.client.post(f"/api/v1/answers/{answer_id}/follow", headers=env.auth(token))
    assert followed.status_code == 201, followed.text

    # Force a material difference by rewriting what was originally delivered,
    # so tonight's identical re-run reads as a substantive change.
    await env.admin.execute(
        "UPDATE public.answers SET content = $2, citations = '[]'::jsonb, "
        "confidence = 'low' WHERE id = $1",
        answer_id,
        "An entirely unrelated prior statement about nothing in particular.",
    )

    tally = await refresh_followed_answers(env.pool, env.redis)
    assert tally["checked"] >= 1
    assert tally["superseded"] >= 1

    versions = await env.client.get(
        f"/api/v1/answers/{answer_id}/versions", headers=env.auth(token)
    )
    assert versions.status_code == 200, versions.text
    body = versions.json()
    assert body["following"] is True
    assert body["superseded"] is True
    assert [v["version"] for v in body["versions"]] == [1, 2]
    # v1 is what the clinician was actually told, preserved verbatim.
    assert body["versions"][0]["content"].startswith("An entirely unrelated prior statement")
    assert body["versions"][1]["diff"]["reasons"], "a supersede must say why"

    # The follower is notified, and subscribers get the event.
    notification = await env.admin.fetchrow(
        "SELECT type, payload FROM public.notifications WHERE org_id = $1 AND user_id = $2",
        org_id,
        user_id,
    )
    assert notification is not None
    assert notification["type"] == "answer_updated"

    event = await env.admin.fetchval(
        "SELECT event FROM public.webhook_deliveries WHERE org_id = $1 AND event = $2",
        org_id,
        "answer.superseded",
    )
    assert event == "answer.superseded"


async def test_unfollowed_answers_are_not_rechecked(env: ApiEnv) -> None:
    org_id, _user_id, token = await env.new_org_with_owner()
    answer_id = (await _ask(env, token, GROUNDED_QUERY))["answer_id"]

    await env.client.post(f"/api/v1/answers/{answer_id}/follow", headers=env.auth(token))
    unfollowed = await env.client.delete(
        f"/api/v1/answers/{answer_id}/follow", headers=env.auth(token)
    )
    assert unfollowed.status_code == 204

    # Rewrite the stored answer so a re-check *would* be material if it ran.
    await env.admin.execute(
        "UPDATE public.answers SET content = 'Something else entirely.' WHERE id = $1",
        answer_id,
    )
    await refresh_followed_answers(env.pool, env.redis)

    # Asserted on this answer, not a global tally: the job spans tenants.
    versions = await env.admin.fetchval(
        "SELECT count(*) FROM public.answer_versions WHERE org_id = $1", org_id
    )
    assert versions == 0, "an unfollowed answer must not be re-checked or superseded"


async def test_versions_are_empty_for_an_answer_that_still_stands(env: ApiEnv) -> None:
    _org_id, _user_id, token = await env.new_org_with_owner()
    answer_id = (await _ask(env, token, GROUNDED_QUERY))["answer_id"]

    response = await env.client.get(
        f"/api/v1/answers/{answer_id}/versions", headers=env.auth(token)
    )
    assert response.status_code == 200
    assert response.json()["superseded"] is False
    assert response.json()["versions"] == []
