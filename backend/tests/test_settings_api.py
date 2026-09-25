"""The Settings page's API: preferences, profile, your data, and voices.

Against the real app, database and Redis; the chat turns these tests create
run offline (no language model), with PubMed and drug labels stood in for.
"""

from __future__ import annotations

import json
import os
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.knowledge.live import LiveResult
from app.voice.voices import VOICES
from tests.conftest import ApiEnv, parse_sse
from tests.voice_doubles import ScriptedSttProvider, ScriptedTtsProvider, scripted_runtime

pytestmark = pytest.mark.skipif(
    not (os.environ.get("TEST_DATABASE_URL") and os.environ.get("TEST_REDIS_URL")),
    reason="needs TEST_DATABASE_URL and TEST_REDIS_URL (the docker compose stack)",
)

QUESTION = "What is first-line anticoagulation in non-valvular atrial fibrillation?"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    class Offline:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        async def enrich(self, question: str, **kwargs: object) -> LiveResult:
            return LiveResult(term=str(kwargs.get("term") or question), searched=True, found=0)

    async def no_labels(_question: str, **_kw: object) -> list[object]:
        return []

    monkeypatch.setattr("app.assistant.chat.LiveLiterature", Offline)
    monkeypatch.setattr("app.assistant.chat.find_labels", no_labels)


async def _chat(env: ApiEnv, token: str, message: str = QUESTION) -> dict[str, Any]:
    response = await env.client.post(
        "/api/v1/chat", json={"message": message}, headers=env.auth(token)
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = next(e for e in parse_sse(response.text) if e["stage"] == "result")[
        "data"
    ]
    return result


# -- preferences -------------------------------------------------------------------------


async def test_preferences_start_as_defaults_then_follow_the_person(env: ApiEnv) -> None:
    _org, _user, token = await env.new_org_with_owner()
    first = await env.client.get("/api/v1/me/preferences", headers=env.auth(token))
    assert first.status_code == 200
    assert first.json()["saved"] is False
    assert first.json()["audience"] == "patient" and first.json()["voice_name"] == "thalia"

    changed = await env.client.patch(
        "/api/v1/me/preferences",
        json={
            "audience": "clinician",
            "voice_name": "harmonia",
            "voice_rate": 1.25,
            "sources_open": True,
            "learn_scope": "pg",
            "followed_specialties": ["cardiology", "neurology", "cardiology"],
        },
        headers=env.auth(token),
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["saved"] is True and body["audience"] == "clinician"
    assert body["followed_specialties"] == ["cardiology", "neurology"]
    assert body["voice_rate"] == 1.25

    # A second change leaves the first one alone, and a fresh read sees both.
    await env.client.patch(
        "/api/v1/me/preferences", json={"show_timeline": False}, headers=env.auth(token)
    )
    again = (await env.client.get("/api/v1/me/preferences", headers=env.auth(token))).json()
    assert again["show_timeline"] is False and again["voice_name"] == "harmonia"


async def test_preferences_refuse_what_the_product_does_not_offer(env: ApiEnv) -> None:
    _org, _user, token = await env.new_org_with_owner()
    for bad in (
        {"voice_name": "hal9000"},
        {"followed_specialties": ["astrology"]},
        {"voice_rate": 3},
        {"audience": "robot"},
        {"favourite_colour": "teal"},
    ):
        response = await env.client.patch(
            "/api/v1/me/preferences", json=bad, headers=env.auth(token)
        )
        assert response.status_code in (400, 422), (bad, response.text)
    unchanged = (await env.client.get("/api/v1/me/preferences", headers=env.auth(token))).json()
    assert unchanged["saved"] is False


# -- profile ---------------------------------------------------------------------------------


async def test_the_profile_changes_name_and_specialty_never_the_role(env: ApiEnv) -> None:
    org_id, _owner, _token = await env.new_org_with_owner()
    viewer_id, viewer_token = await env.add_member(org_id, "viewer")
    updated = await env.client.patch(
        "/api/v1/me/profile",
        json={"full_name": "  Dr Asha Rao ", "specialty": "Cardiology"},
        headers=env.auth(viewer_token),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["full_name"] == "Dr Asha Rao"
    assert updated.json()["specialty"] == "Cardiology"
    assert updated.json()["role"] == "viewer"

    sneaky = await env.client.patch(
        "/api/v1/me/profile", json={"role": "owner"}, headers=env.auth(viewer_token)
    )
    assert sneaky.status_code == 422
    role = await env.admin.fetchval("SELECT role FROM public.profiles WHERE id = $1", viewer_id)
    assert role == "viewer"

    cleared = await env.client.patch(
        "/api/v1/me/profile", json={"specialty": ""}, headers=env.auth(viewer_token)
    )
    assert cleared.json()["specialty"] is None


# -- your data --------------------------------------------------------------------------------


async def test_an_export_is_a_copy_of_the_persons_own_conversations(env: ApiEnv) -> None:
    _org, user_id, token = await env.new_org_with_owner()
    await env.redis.delete(f"rl:export:{user_id}")
    await _chat(env, token)
    response = await env.client.get("/api/v1/me/export", headers=env.auth(token))
    assert response.status_code == 200, response.text
    assert "attachment" in response.headers["content-disposition"]
    data = json.loads(response.content)
    assert data["format"] == "clinicalcontext-export/1"
    assert data["profile"]["user_id"] == str(user_id)
    assert [c["kind"] for c in data["conversations"]] == ["chat"]
    turn = data["conversations"][0]["turns"][0]
    assert turn["question"] == QUESTION and turn["answer"]
    source_keys = {"marker", "title", "journal", "pmid", "doi", "url"}
    assert all(set(source) == source_keys for source in turn["sources"])


async def test_a_conversation_can_be_deleted_by_its_owner_only(env: ApiEnv) -> None:
    org_id, _user, token = await env.new_org_with_owner()
    session_id = (await _chat(env, token))["session_id"]
    _colleague, colleague_token = await env.add_member(org_id, "clinician")

    theirs = await env.client.delete(
        f"/api/v1/chat/sessions/{session_id}", headers=env.auth(colleague_token)
    )
    assert theirs.status_code == 404

    mine = await env.client.delete(f"/api/v1/chat/sessions/{session_id}", headers=env.auth(token))
    assert mine.status_code == 200 and mine.json() == {"deleted": 1, "kept": 0}
    listed = await env.client.get("/api/v1/chat/sessions", headers=env.auth(token))
    assert listed.json() == []
    questions = await env.admin.fetchval(
        "SELECT count(*) FROM public.queries WHERE session_id = $1", UUID(session_id)
    )
    assert questions == 0


async def test_deleting_everything_keeps_searches_and_shared_answers(env: ApiEnv) -> None:
    org_id, user_id, token = await env.new_org_with_owner()
    await _chat(env, token)
    shared = await _chat(env, token, "What is the first-line treatment for scrub typhus?")
    await env.admin.execute(
        "INSERT INTO public.share_links (org_id, answer_id, slug, created_by) "
        "VALUES ($1, $2, $3, $4)",
        org_id,
        UUID(shared["answer_id"]),
        f"s-{uuid4().hex[:10]}",
        user_id,
    )
    search_id = await env.admin.fetchval(
        "INSERT INTO public.query_sessions (org_id, user_id, title, kind) "
        "VALUES ($1, $2, 'An evidence search', 'ask') RETURNING id",
        org_id,
        user_id,
    )

    response = await env.client.delete("/api/v1/me/conversations", headers=env.auth(token))
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 1, "kept": 1}
    left = await env.admin.fetch(
        "SELECT id, kind FROM public.query_sessions WHERE user_id = $1 ORDER BY kind", user_id
    )
    assert {r["kind"] for r in left} == {"ask", "chat"}
    assert search_id in {r["id"] for r in left}
    audit = await env.admin.fetchval(
        "SELECT payload FROM public.audit_log "
        "WHERE org_id = $1 AND action = 'conversations.deleted'",
        org_id,
    )
    assert json.loads(audit) == {"deleted": 1, "kept": 1}


# -- voices ----------------------------------------------------------------------------------------


async def test_the_chosen_voice_reaches_a_speaker_that_offers_voices(env: ApiEnv) -> None:
    class Deepgramish(ScriptedTtsProvider):
        name = "deepgram"

    tts = Deepgramish()
    env.app.state.voice_runtime = scripted_runtime(ScriptedSttProvider([]), tts)
    _org, user_id, token = await env.new_org_with_owner()
    await env.redis.delete(f"rl:speak:{user_id}")

    listed = await env.client.get("/api/v1/voice/voices", headers=env.auth(token))
    assert listed.status_code == 200
    assert listed.json()["selectable"] is True and listed.json()["default"] == "thalia"
    assert [v["id"] for v in listed.json()["voices"]] == [v.id for v in VOICES]

    for voice, expected in (("draco", "aura-2-draco-en"), ("retired", None), (None, None)):
        body: dict[str, Any] = {"text": "Take it with food."}
        if voice:
            body["voice"] = voice
        spoken = await env.client.post("/api/v1/voice/speak", json=body, headers=env.auth(token))
        assert spoken.status_code == 200, spoken.text
        assert tts.voices[-1] == expected


async def test_a_server_without_voice_choice_says_so(env: ApiEnv) -> None:
    tts = ScriptedTtsProvider()
    env.app.state.voice_runtime = scripted_runtime(ScriptedSttProvider([]), tts)
    _org, user_id, token = await env.new_org_with_owner()
    await env.redis.delete(f"rl:speak:{user_id}")
    listed = await env.client.get("/api/v1/voice/voices", headers=env.auth(token))
    assert listed.json()["selectable"] is False
    spoken = await env.client.post(
        "/api/v1/voice/speak",
        json={"text": "Take it with food.", "voice": "draco"},
        headers=env.auth(token),
    )
    assert spoken.status_code == 200
    assert tts.voices[-1] is None
