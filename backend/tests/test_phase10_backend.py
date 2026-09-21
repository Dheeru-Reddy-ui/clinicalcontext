"""Phase 10 backend support: persisted reasoning, binders, sharing, notifications.

Real Postgres + Redis (see conftest). Everything the frontend needs after the
live stream is gone must be readable back — that is what these tests pin down.
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest

from tests.conftest import ApiEnv, parse_sse

pytestmark = pytest.mark.skipif(
    not (os.environ.get("TEST_DATABASE_URL") and os.environ.get("TEST_REDIS_URL")),
    reason="TEST_DATABASE_URL/TEST_REDIS_URL not set (needs a running Postgres + Redis)",
)

GROUNDED_QUERY = "How is type 2 diabetes managed with metformin?"


async def _ask(env: ApiEnv, token: str, query: str = GROUNDED_QUERY) -> dict[str, object]:
    response = await env.client.post(
        "/api/v1/queries", json={"query": query}, headers=env.auth(token)
    )
    assert response.status_code == 200, response.text
    result = next(e for e in parse_sse(response.text) if e["stage"] == "result")
    data: dict[str, object] = result["data"]
    return data


# -- reasoning survives persistence ---------------------------------------------------


async def test_structured_reasoning_is_persisted_and_served_on_reload(env: ApiEnv) -> None:
    _org, _user, token = await env.new_org_with_owner()
    live = await _ask(env, token)
    assert "reasoning" in live, "live result event carries the structured reasoning"

    detail = await env.client.get(f"/api/v1/queries/{live['query_id']}", headers=env.auth(token))
    assert detail.status_code == 200, detail.text
    stored = detail.json()["answer"]["reasoning"]
    # Everything the contradiction/abstention/timeline/footer views need on reload.
    for key in (
        "contradiction",
        "sub_questions",
        "retrieval_grade",
        "rewrite_count",
        "generation_mode",
        "prompt_versions",
        "query_type",
    ):
        assert key in stored, f"reasoning.{key} must survive persistence"
    assert stored["contradiction"]["detected"] == live["contradiction"]["detected"]  # type: ignore[index]
    assert stored["retrieval_grade"] in ("sufficient", "insufficient", "irrelevant")


# -- binders --------------------------------------------------------------------------


async def test_binder_holds_answers_and_passages_with_threaded_annotations(env: ApiEnv) -> None:
    _org, _user, token = await env.new_org_with_owner()
    answer = await _ask(env, token)
    citations = answer["citations"]
    assert isinstance(citations, list) and citations, "grounded answer has citations"
    chunk_id = citations[0]["chunk_id"]

    created = await env.client.post(
        "/api/v1/binders",
        json={"title": "Journal club — metformin", "visibility": "org"},
        headers=env.auth(token),
    )
    assert created.status_code == 201, created.text
    binder_id = created.json()["id"]

    a = await env.client.post(
        f"/api/v1/binders/{binder_id}/items",
        json={"item_type": "answer", "answer_id": answer["answer_id"]},
        headers=env.auth(token),
    )
    assert a.status_code == 201, a.text
    assert a.json()["answer"]["query"] == GROUNDED_QUERY
    assert "reasoning" in a.json()["answer"]

    p = await env.client.post(
        f"/api/v1/binders/{binder_id}/items",
        json={"item_type": "passage", "chunk_id": chunk_id},
        headers=env.auth(token),
    )
    assert p.status_code == 201, p.text
    passage_item = p.json()
    assert passage_item["passage"]["document_title"]
    assert passage_item["passage"]["content"]

    # Highlight-and-annotate, then reply in the thread.
    note = await env.client.post(
        f"/api/v1/binders/{binder_id}/items/{passage_item['id']}/annotations",
        json={
            "body": "Key sentence for the discussion.",
            "highlight_range": {"start": 0, "end": 40},
        },
        headers=env.auth(token),
    )
    assert note.status_code == 201, note.text
    reply = await env.client.post(
        f"/api/v1/binders/{binder_id}/items/{passage_item['id']}/annotations",
        json={"body": "Agreed — compare with the 2019 trial.", "parent_id": note.json()["id"]},
        headers=env.auth(token),
    )
    assert reply.status_code == 201, reply.text

    detail = await env.client.get(f"/api/v1/binders/{binder_id}", headers=env.auth(token))
    assert detail.status_code == 200
    body = detail.json()
    assert body["binder"]["item_count"] == 2
    items = {i["item_type"]: i for i in body["items"]}
    threads = items["passage"]["annotations"]
    assert len(threads) == 1 and threads[0]["highlight_range"] == {"start": 0, "end": 40}
    assert [r["body"] for r in threads[0]["replies"]] == ["Agreed — compare with the 2019 trial."]


async def test_private_binder_is_invisible_to_colleagues_but_org_binder_is_shared(
    env: ApiEnv,
) -> None:
    org_id, _owner, owner_token = await env.new_org_with_owner()
    _member, member_token = await env.add_member(org_id, "clinician")

    private = await env.client.post(
        "/api/v1/binders", json={"title": "My notes"}, headers=env.auth(owner_token)
    )
    shared = await env.client.post(
        "/api/v1/binders",
        json={"title": "Team binder", "visibility": "org"},
        headers=env.auth(owner_token),
    )
    assert private.status_code == 201 and shared.status_code == 201

    seen = await env.client.get("/api/v1/binders", headers=env.auth(member_token))
    titles = {b["title"] for b in seen.json()["binders"]}
    assert titles == {"Team binder"}
    hidden = await env.client.get(
        f"/api/v1/binders/{private.json()['id']}", headers=env.auth(member_token)
    )
    assert hidden.status_code == 404  # indistinguishable from nonexistent


async def test_viewer_can_read_org_binders_but_not_curate(env: ApiEnv) -> None:
    org_id, _owner, owner_token = await env.new_org_with_owner()
    _viewer, viewer_token = await env.add_member(org_id, "viewer")
    await env.client.post(
        "/api/v1/binders",
        json={"title": "Shared", "visibility": "org"},
        headers=env.auth(owner_token),
    )
    assert (
        await env.client.get("/api/v1/binders", headers=env.auth(viewer_token))
    ).status_code == 200
    denied = await env.client.post(
        "/api/v1/binders", json={"title": "Nope"}, headers=env.auth(viewer_token)
    )
    assert denied.status_code == 403


# -- sharing + public permalinks ------------------------------------------------------


async def test_permalink_renders_logged_out_and_dies_on_revoke(env: ApiEnv) -> None:
    _org, _user, token = await env.new_org_with_owner()
    answer = await _ask(env, token)

    link = await env.client.post(
        "/api/v1/sharing/links",
        json={"answer_id": answer["answer_id"]},
        headers=env.auth(token),
    )
    assert link.status_code == 201, link.text
    slug = link.json()["slug"]
    assert link.json()["path"] == f"/a/{slug}"

    # No credentials at all: the public surface, outside /api/v1.
    public = await env.client.get(f"/api/public/answers/{slug}")
    assert public.status_code == 200, public.text
    page = public.json()
    assert page["query"] == GROUNDED_QUERY
    assert page["content"] == answer["answer"]
    assert page["citations"] and page["confidence"]
    assert page["superseded"] is None
    assert "reasoning" in page
    # Nothing that identifies a user or exposes internal ids.
    assert not {"user_id", "org_id", "query_id", "answer_id"} & set(page)

    # Re-sharing the same answer returns the same live link, not a second one.
    again = await env.client.post(
        "/api/v1/sharing/links",
        json={"answer_id": answer["answer_id"]},
        headers=env.auth(token),
    )
    assert again.json()["slug"] == slug

    revoked = await env.client.delete(
        f"/api/v1/sharing/links/{link.json()['id']}", headers=env.auth(token)
    )
    assert revoked.status_code == 204
    gone = await env.client.get(f"/api/public/answers/{slug}")
    assert gone.status_code == 404
    assert gone.json()["error"]["code"] == "not_found"


async def test_org_level_sharing_switch_kills_every_link_with_a_clean_404(env: ApiEnv) -> None:
    org_id, _owner, owner_token = await env.new_org_with_owner()
    _clin, clin_token = await env.add_member(org_id, "clinician")
    answer = await _ask(env, clin_token)
    slug = (
        await env.client.post(
            "/api/v1/sharing/links",
            json={"answer_id": answer["answer_id"]},
            headers=env.auth(clin_token),
        )
    ).json()["slug"]
    assert (await env.client.get(f"/api/public/answers/{slug}")).status_code == 200

    # A clinician cannot flip the tenant policy; the owner can.
    forbidden = await env.client.patch(
        "/api/v1/sharing/policy",
        json={"public_sharing_enabled": False},
        headers=env.auth(clin_token),
    )
    assert forbidden.status_code == 403
    off = await env.client.patch(
        "/api/v1/sharing/policy",
        json={"public_sharing_enabled": False},
        headers=env.auth(owner_token),
    )
    assert off.status_code == 200 and off.json()["public_sharing_enabled"] is False

    dead = await env.client.get(f"/api/public/answers/{slug}")
    assert dead.status_code == 404
    assert dead.json()["error"]["code"] == "not_found"
    # The link row itself is untouched: switching sharing back on revives it.
    await env.client.patch(
        "/api/v1/sharing/policy",
        json={"public_sharing_enabled": True},
        headers=env.auth(owner_token),
    )
    assert (await env.client.get(f"/api/public/answers/{slug}")).status_code == 200


async def test_unknown_slug_is_404_not_500(env: ApiEnv) -> None:
    response = await env.client.get(f"/api/public/answers/{uuid4().hex[:12]}")
    assert response.status_code == 404


async def test_every_v1_route_still_requires_credentials(env: ApiEnv) -> None:
    """The public surface must not have leaked an anonymous route into /api/v1."""
    schema = env.app.openapi()
    v1 = [p for p in schema["paths"] if p.startswith("/api/v1")]
    assert any("/binders" in p for p in v1) and any("/sharing" in p for p in v1)
    for path in ("/api/v1/binders", "/api/v1/sharing/links", "/api/v1/notifications"):
        assert (await env.client.get(path)).status_code == 401
    assert all(not p.startswith("/api/v1") for p in schema["paths"] if "/public/" in p)


# -- notifications --------------------------------------------------------------------


async def test_notification_center_lists_and_marks_read(env: ApiEnv) -> None:
    org_id, user_id, token = await env.new_org_with_owner()
    _other, other_token = await env.add_member(org_id, "clinician")
    # Notifications are system-written (Living Answers, invites): seed one directly.
    note_id = await env.admin.fetchval(
        "INSERT INTO public.notifications (org_id, user_id, type, payload) "
        "VALUES ($1, $2, 'answer_updated', '{\"version\": 2}'::jsonb) RETURNING id",
        org_id,
        user_id,
    )

    mine = await env.client.get("/api/v1/notifications", headers=env.auth(token))
    assert mine.status_code == 200
    assert mine.json()["unread"] == 1
    assert mine.json()["notifications"][0]["payload"] == {"version": 2}

    # Personal: a colleague in the same org does not see it.
    theirs = await env.client.get("/api/v1/notifications", headers=env.auth(other_token))
    assert theirs.json()["unread"] == 0 and theirs.json()["notifications"] == []

    read = await env.client.post(f"/api/v1/notifications/{note_id}/read", headers=env.auth(token))
    assert read.status_code == 200 and read.json()["read_at"] is not None
    assert (await env.client.get("/api/v1/notifications", headers=env.auth(token))).json()[
        "unread"
    ] == 0
    # Marking someone else's is a 404, not a silent no-op.
    assert (
        await env.client.post(
            f"/api/v1/notifications/{note_id}/read", headers=env.auth(other_token)
        )
    ).status_code == 404
    assert isinstance(UUID(str(note_id)), UUID)


# -- analytics: guardrail breakdown ---------------------------------------------------


async def test_analytics_break_guardrail_triggers_down_by_type(env: ApiEnv) -> None:
    _org, _user, token = await env.new_org_with_owner()
    # One PHI block, one clean query.
    await env.client.post(
        "/api/v1/queries",
        json={"query": "What anticoagulant for patient John Smith, DOB 03/14/1982?"},
        headers=env.auth(token),
    )
    await _ask(env, token)
    overview = await env.client.get("/api/v1/analytics/overview", headers=env.auth(token))
    assert overview.status_code == 200
    triggers = overview.json()["guardrail_triggers"]
    assert set(triggers) == {"phi", "scope", "red_flag"}
    assert triggers["phi"] == 1
    assert overview.json()["blocked"] == 1
