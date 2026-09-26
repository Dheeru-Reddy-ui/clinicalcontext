"""Learn's tools against the real app and database: the AI tutor's quizzes and
practice record, the note summarizer, and Ask-this-Paper end to end.

As in the chat tests, the two things that leave the machine are stood in for
— the language model by an OpenAI-compatible MockTransport, PubMed by a
recorder. Retrieval, guardrails, row-level security and persistence are real.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.assistant.chat import ChatEvidence
from app.knowledge.live import LiveResult
from app.llm.chat import ChatModel, ChatProvider
from app.repositories.base import tenant_connection
from app.retrieval.types import RetrievedChunk
from tests.conftest import ApiEnv, parse_sse
from tests.test_learn_units import NOTE, make_paper

pytestmark = pytest.mark.skipif(
    not (os.environ.get("TEST_DATABASE_URL") and os.environ.get("TEST_REDIS_URL")),
    reason="needs TEST_DATABASE_URL and TEST_REDIS_URL (the docker compose stack)",
)


class _NoLive:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def enrich(self, question: str, **kwargs: object) -> LiveResult:
        return LiveResult(term=str(kwargs.get("term") or question), searched=True, found=0)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.assistant.chat.LiveLiterature", _NoLive)

    async def no_labels(_question: str, **_kw: object) -> list[object]:
        return []

    monkeypatch.setattr("app.assistant.chat.find_labels", no_labels)


def _model_class(reply: str, seen: list[dict[str, Any]]) -> type[ChatModel]:
    """A ChatModel class that answers ``reply`` — as one JSON completion, or
    streamed, whichever the caller asks for."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if body.get("stream"):
            chunks = [
                {"choices": [{"delta": {"content": reply}}]},
                {"choices": [], "usage": {"prompt_tokens": 700, "completion_tokens": 90}},
            ]
            text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, content=text.encode())
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": reply}}],
                "usage": {"prompt_tokens": 700, "completion_tokens": 90},
            },
        )

    provider = ChatProvider(
        "groq", "https://api.groq.com/openai/v1", "k-test", "openai/gpt-oss-120b"
    )

    class Fake(ChatModel):
        def __init__(self, *_a: object, **_k: object) -> None:
            super().__init__([provider], transport=httpx.MockTransport(handler))

    return Fake


# -- the AI tutor ------------------------------------------------------------------------


async def test_without_a_model_a_quiz_is_fill_in_the_blank_from_the_library(env: ApiEnv) -> None:
    _, _, token = await env.new_org_with_owner()
    response = await env.client.post(
        "/api/v1/learn/tutor/quiz",
        json={
            "topic": "apixaban warfarin stroke prevention in atrial fibrillation",
            "level": "mbbs",
            "count": 3,
        },
        headers=env.auth(token),
    )
    assert response.status_code == 200, response.text
    quiz = response.json()
    assert quiz["generated_by"] == "offline" and quiz["notices"]
    assert 2 <= len(quiz["questions"]) <= 3 and quiz["sources"]
    for question in quiz["questions"]:
        assert "_____" in question["stem"] and len(question["options"]) == 4
        assert 0 <= question["answer"] < 4
        assert all(1 <= n <= len(quiz["sources"]) for n in question["sources"])


async def test_a_quiz_from_the_model_keeps_only_checked_questions(
    env: ApiEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        content="In atrial fibrillation, apixaban reduced stroke or systemic embolism compared "
        "with warfarin and caused less major bleeding.",
        section="Results",
        title="Apixaban versus warfarin",
        publication_date=date(2011, 9, 15),
        evidence_grade="A",
        study_type="randomized_controlled_trial",
    )

    class PinnedEvidence:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        async def gather_evidence(self, question: str, **_kw: object) -> Any:
            yield ChatEvidence(chunks=[source], retrieval_query=question, coverage=1.0)

    monkeypatch.setattr("app.api.v1.learn.ChatAssistant", PinnedEvidence)
    good = {
        "stem": "Which drug reduced stroke with less major bleeding than warfarin in AF?",
        "options": ["Apixaban", "Aspirin", "Digoxin", "Amiodarone"],
        "answer": 0,
        "explanation": "Apixaban reduced stroke or systemic embolism compared with warfarin, "
        "with less major bleeding [1].",
        "sources": [1],
    }
    invented = {
        **good,
        "stem": "By how much did apixaban cut strokes?",
        "explanation": ("Apixaban cut strokes by 80% compared with warfarin [1]."),
    }
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.api.v1.learn.ChatModel",
        _model_class(json.dumps({"questions": [good, invented]}), seen),
    )
    _, _, token = await env.new_org_with_owner()
    response = await env.client.post(
        "/api/v1/learn/tutor/quiz",
        json={"topic": "anticoagulation in AF", "specialty": "cardiology", "count": 2},
        headers=env.auth(token),
    )
    assert response.status_code == 200, response.text
    quiz = response.json()
    assert quiz["generated_by"] == "llm" and quiz["dropped"] == 1
    assert [q["stem"] for q in quiz["questions"]] == [good["stem"]]
    assert quiz["sources"][0]["title"] == "Apixaban versus warfarin"
    request = seen[0]
    assert request["response_format"] == {"type": "json_object"}
    assert "SPECIALTY: Cardiology" in request["messages"][1]["content"]


async def test_a_case_needs_the_ai_writer(env: ApiEnv) -> None:
    _, _, token = await env.new_org_with_owner()
    response = await env.client.post(
        "/api/v1/learn/tutor/quiz",
        json={"topic": "atrial fibrillation", "mode": "case", "count": 3},
        headers=env.auth(token),
    )
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "learn_tool_unavailable"


def _attempt(topic: str, correct: bool, n: int) -> dict[str, Any]:
    return {
        "mode": "quiz",
        "level": "mbbs",
        "specialty": "pulmonology",
        "topic": topic,
        "question": f"Question {n} about {topic}?",
        "chosen": "B",
        "answer": "A" if not correct else "B",
        "explanation": f"Because of fact {n}.",
        "correct": correct,
    }


async def test_practice_shows_progress_and_stays_the_learners_own(env: ApiEnv) -> None:
    org_id, owner_id, token = await env.new_org_with_owner()
    member_id, member_token = await env.add_member(org_id, "clinician")
    answers = [("Asthma", True), ("Asthma", False), ("asthma", False), ("Asthma", False)]
    answers.append(("COPD", True))
    for n, (topic, correct) in enumerate(answers):
        response = await env.client.post(
            "/api/v1/learn/tutor/attempts",
            json=_attempt(topic, correct, n),
            headers=env.auth(token),
        )
        assert response.status_code == 201, response.text

    progress = (
        await env.client.get("/api/v1/learn/tutor/progress", headers=env.auth(token))
    ).json()
    assert progress["answered"] == 5 and progress["correct"] == 2
    assert progress["streak_days"] >= 1 and progress["week_answered"] == 5
    asthma = next(t for t in progress["topics"] if t["topic"].lower() == "asthma")
    assert asthma["answered"] == 4 and asthma["accuracy"] == 0.25
    assert [t["topic"].lower() for t in progress["weakest"]] == ["asthma"]
    assert len(progress["mistakes"]) == 3 and progress["mistakes"][0]["explanation"]

    # Another member sees none of it — through the API or straight from the table.
    theirs = await env.client.get("/api/v1/learn/tutor/progress", headers=env.auth(member_token))
    assert theirs.json()["answered"] == 0
    async with tenant_connection(env.pool, org_id, member_id) as conn:
        assert await conn.fetchval("SELECT count(*) FROM public.tutor_attempts") == 0
    async with tenant_connection(env.pool, org_id, owner_id) as conn:
        assert await conn.fetchval("SELECT count(*) FROM public.tutor_attempts") == 5

    export = await env.client.get("/api/v1/me/export", headers=env.auth(token))
    assert len(export.json()["tutor_practice"]) == 5

    cleared = await env.client.delete("/api/v1/learn/tutor/progress", headers=env.auth(token))
    assert cleared.json() == {"deleted": 5}
    again = await env.client.get("/api/v1/learn/tutor/progress", headers=env.auth(token))
    assert again.json()["answered"] == 0


async def test_a_tutor_lesson_is_taught_in_the_tutors_voice(
    env: ApiEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.assistant.chat.ChatModel",
        _model_class("Heart failure is ... Your turn: what does BNP measure?", seen),
    )
    _, _, token = await env.new_org_with_owner()
    response = await env.client.post(
        "/api/v1/chat",
        json={
            "message": "Teach me heart failure",
            "audience": "student",
            "kind": "tutor",
            "specialty": "cardiology",
            "level": "mbbs",
        },
        headers=env.auth(token),
    )
    assert response.status_code == 200
    result = next(e for e in parse_sse(response.text) if e["stage"] == "result")
    assert "Your turn" in result["data"]["answer"]
    system = seen[0]["messages"][0]["content"]
    assert "AI medical tutor" in system and "Your turn" in system
    sessions = await env.client.get(
        "/api/v1/chat/sessions", params={"kind": "tutor"}, headers=env.auth(token)
    )
    assert [s["kind"] for s in sessions.json()] == ["tutor"]


# -- the clinical note summarizer ----------------------------------------------------------


async def test_a_note_is_summarized_without_its_identifiers_and_nothing_is_kept(
    env: ApiEnv,
) -> None:
    org_id, _, token = await env.new_org_with_owner()

    async def stored() -> int:
        return int(
            await env.admin.fetchval(
                "SELECT (SELECT count(*) FROM public.queries WHERE org_id = $1) "
                "+ (SELECT count(*) FROM public.answers WHERE org_id = $1)",
                org_id,
            )
        )

    before = await stored()
    response = await env.client.post(
        "/api/v1/learn/notes/summarize", json={"text": NOTE}, headers=env.auth(token)
    )
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["mode"] == "extractive" and summary["notice"]
    assert summary["redactions"]["NAME"] >= 2 and summary["redactions"]["DATE"] == 2
    everything = json.dumps(summary)
    for identifier in ("Ramesh", "HYD-2024-004829", "98480", "12/03/2024"):
        assert identifier not in everything
    headings = [s["heading"] for s in summary["sections"]]
    assert "Medications" in headings and "Problems" in headings
    assert await stored() == before, "neither the note nor its summary is stored"


async def test_a_report_file_is_read_back_as_text(env: ApiEnv, tmp_path: Path) -> None:
    _, _, token = await env.new_org_with_owner()
    as_text = await env.client.post(
        "/api/v1/learn/notes/text",
        files={"file": ("report.txt", NOTE.encode(), "text/plain")},
        headers=env.auth(token),
    )
    assert as_text.status_code == 200 and "DISCHARGE SUMMARY" in as_text.json()["text"]
    pdf = make_paper(tmp_path / "report.pdf").read_bytes()
    as_pdf = await env.client.post(
        "/api/v1/learn/notes/text",
        files={"file": ("report.pdf", pdf, "application/pdf")},
        headers=env.auth(token),
    )
    assert as_pdf.status_code == 200 and as_pdf.json()["pages"] == 3
    refused = await env.client.post(
        "/api/v1/learn/notes/text",
        files={"file": ("report.docx", b"PK\x03\x04", "application/octet-stream")},
        headers=env.auth(token),
    )
    assert refused.status_code == 415


# -- Ask-this-Paper -------------------------------------------------------------------------


async def test_ask_this_paper_end_to_end(env: ApiEnv, tmp_path: Path) -> None:
    org_id, _, token = await env.new_org_with_owner()
    _, member_token = await env.add_member(org_id, "clinician")
    pdf = make_paper(tmp_path / "trial.pdf").read_bytes()

    uploaded = await env.client.post(
        "/api/v1/learn/papers",
        files={"file": ("trial.pdf", pdf, "application/pdf")},
        headers=env.auth(token),
    )
    assert uploaded.status_code == 201, uploaded.text
    paper = uploaded.json()
    paper_id = paper["id"]
    try:
        assert paper["pages"] == 3 and paper["chunk_count"] >= 4 and paper["uploaded_by_you"]
        assert paper["title"].startswith("Apixaban versus Aspirin")
        assert paper["study_type"] == "randomized_controlled_trial"

        again = await env.client.post(
            "/api/v1/learn/papers",
            files={"file": ("trial-copy.pdf", pdf, "application/pdf")},
            headers=env.auth(token),
        )
        assert again.json()["id"] == paper_id and again.json()["deduplicated"]

        listed = await env.client.get("/api/v1/learn/papers", headers=env.auth(member_token))
        assert [p["id"] for p in listed.json()] == [paper_id]
        assert not listed.json()[0]["uploaded_by_you"]

        chat = await env.client.post(
            "/api/v1/chat",
            json={
                "message": "What was the hazard ratio for stroke?",
                "audience": "student",
                "kind": "paper",
                "document_id": paper_id,
            },
            headers=env.auth(token),
        )
        assert chat.status_code == 200
        result = next(e for e in parse_sse(chat.text) if e["stage"] == "result")
        assert "hazard ratio 0.45" in result["data"]["answer"]
        sections = {c["section"] for c in result["data"]["citations"]}
        assert "Results · p. 2" in sections
        assert result["data"]["kind"] == "paper"

        detail = (
            await env.client.get(f"/api/v1/learn/papers/{paper_id}", headers=env.auth(token))
        ).json()
        assert {"title": "Results", "page": 2} in detail["outline"]
        assert len(detail["conversations"]) == 1
        assert detail["abstract"] and "reduced stroke" in detail["abstract"]

        refused = await env.client.delete(
            f"/api/v1/learn/papers/{paper_id}", headers=env.auth(member_token)
        )
        assert refused.status_code == 403
        deleted = await env.client.delete(
            f"/api/v1/learn/papers/{paper_id}", headers=env.auth(token)
        )
        assert deleted.json() == {
            "deleted": True,
            "conversations_deleted": 1,
            "conversations_kept": 0,
        }
        gone = await env.client.get(f"/api/v1/learn/papers/{paper_id}", headers=env.auth(token))
        assert gone.status_code == 404
    finally:
        await env.admin.execute("DELETE FROM public.documents WHERE id = $1", UUID(paper_id))


async def test_another_workspaces_paper_cannot_be_asked_or_opened(
    env: ApiEnv, tmp_path: Path
) -> None:
    _, _, token = await env.new_org_with_owner()
    _, _, stranger = await env.new_org_with_owner()
    pdf = make_paper(tmp_path / "trial.pdf").read_bytes()
    uploaded = await env.client.post(
        "/api/v1/learn/papers",
        files={"file": ("trial.pdf", pdf, "application/pdf")},
        headers=env.auth(token),
    )
    paper_id = uploaded.json()["id"]
    try:
        asked = await env.client.post(
            "/api/v1/chat",
            json={"message": "Summarise it", "kind": "paper", "document_id": paper_id},
            headers=env.auth(stranger),
        )
        assert asked.status_code == 404
        opened = await env.client.get(
            f"/api/v1/learn/papers/{paper_id}", headers=env.auth(stranger)
        )
        assert opened.status_code == 404
        assert (
            await env.client.get("/api/v1/learn/papers", headers=env.auth(stranger))
        ).json() == []
    finally:
        await env.admin.execute("DELETE FROM public.documents WHERE id = $1", UUID(paper_id))
