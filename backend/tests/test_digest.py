"""Phase 13: the weekly evidence digest — opt-in per user, built from the
rows the system already writes, delivered in-app and (opt-in) by email."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.services import digest
from app.services.ask import AskService
from app.services.suggest import CHECK_TAGS
from tests.conftest import ApiEnv

GROUNDED_QUERY = "How is type 2 diabetes managed with metformin?"


async def test_preferences_are_off_by_default_and_email_needs_the_digest(env: ApiEnv) -> None:
    _org, _user, token = await env.new_org_with_owner()
    response = await env.client.get("/api/v1/digest/preferences", headers=env.auth(token))
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "email": False, "last_sent_at": None}

    # Email alone does nothing: it is a copy of the in-app digest.
    response = await env.client.patch(
        "/api/v1/digest/preferences", json={"email": True}, headers=env.auth(token)
    )
    assert response.json()["enabled"] is False and response.json()["email"] is False

    response = await env.client.patch(
        "/api/v1/digest/preferences", json={"enabled": True, "email": True}, headers=env.auth(token)
    )
    assert response.json()["enabled"] is True and response.json()["email"] is True

    # Turning the digest off turns the email copy off with it.
    response = await env.client.patch(
        "/api/v1/digest/preferences", json={"enabled": False}, headers=env.auth(token)
    )
    assert response.json() == {"enabled": False, "email": False, "last_sent_at": None}


async def test_digest_is_built_from_what_the_user_asked_and_delivered_once(
    env: ApiEnv, monkeypatch: Any
) -> None:
    org_id, user_id, token = await env.new_org_with_owner()
    now = datetime.now(UTC)
    since = now - timedelta(days=7)

    # A real question, so the user has topics: the MeSH headings of the
    # documents the answer cited.
    events = [
        e
        async for e in AskService(env.pool, env.redis).ask(
            query=GROUNDED_QUERY, org_id=org_id, user_id=user_id
        )
    ]
    result = next(e for e in events if e["stage"] == "result")["data"]
    assert result["citations"], "the question must ground for the digest to have topics"
    cited_doc = result["citations"][0]["document_id"]
    mesh = await env.admin.fetchval(
        "SELECT metadata -> 'mesh_terms' FROM public.documents WHERE id = $1", cited_doc
    )
    headings = json.loads(mesh) if isinstance(mesh, str) else (mesh or [])
    # A topic is a heading, not a check tag ("Humans", "Female") — those are
    # on nearly every record and say nothing about what was asked.
    mesh_terms = [h for h in headings if h.lower() not in CHECK_TAGS]
    assert mesh_terms, "the seeded corpus carries MeSH headings beyond the check tags"

    # "New evidence" is a document ingested this week that carries one of the
    # user's topics — here, one inserted the way the freshness job would.
    salt = uuid4().hex[:8]
    new_doc = await env.admin.fetchval(
        """
        INSERT INTO public.documents
            (org_id, source_type, title, content_hash, journal, publication_date,
             evidence_grade, metadata)
        VALUES (NULL, 'pubmed', $1, $2, 'Digest Test Journal', current_date, 'A', $3::jsonb)
        RETURNING id
        """,
        f"New evidence for the digest [{salt}]",
        f"digest-{salt}",
        json.dumps({"mesh_terms": [mesh_terms[0]]}),
    )
    try:
        # The window closes after the insert: the pipeline run above took real time.
        until = datetime.now(UTC) + timedelta(seconds=5)
        built = await digest.build_digest(
            env.pool, org_id=org_id, user_id=user_id, since=since, until=until
        )
        assert mesh_terms[0] in built["topics"]
        titles = [d["title"] for d in built["new_documents"]]
        assert f"New evidence for the digest [{salt}]" in titles
        listed = next(d for d in built["new_documents"] if d["title"].endswith(f"[{salt}]"))
        assert listed["topics"] == [mesh_terms[0]] and listed["evidence_grade"] == "A"
        assert built["usage"]["queries"] == 1
        assert built["answer_changes"] == []
        assert not digest.is_empty(built)

        # Delivery: in-app always; email only when opted in and SMTP is set —
        # the transport is replaced, the message it would send is checked.
        sent: list[dict[str, str]] = []

        def fake_send(settings: Any, *, to: str, subject: str, body: str) -> None:
            sent.append({"to": to, "subject": subject, "body": body})

        monkeypatch.setattr(digest, "_send_email_sync", fake_send)
        monkeypatch.setattr(get_settings(), "smtp_host", "smtp.test")
        await digest.set_preferences(
            env.pool, org_id=org_id, user_id=user_id, enabled=True, email=True
        )

        summary = await digest.run_weekly_digest(env.pool, now=until)
        assert summary["sent"] >= 1 and summary["emailed"] >= 1
        notifications = await env.admin.fetch(
            "SELECT type, payload FROM public.notifications WHERE user_id = $1", user_id
        )
        mine = [n for n in notifications if n["type"] == "weekly_digest"]
        assert len(mine) == 1
        payload = json.loads(mine[0]["payload"])
        assert payload["usage"]["queries"] == 1 and payload["new_documents"]
        email = next(m for m in sent if f"[{salt}]" in m["body"])
        assert email["subject"].startswith("ClinicalContext weekly digest")
        assert "New evidence in your topics (" in email["body"]
        assert "Your organisation this week" in email["body"]
        assert "1 questions" in email["body"]

        # The API shows when it went out, and the job does not send twice.
        response = await env.client.get("/api/v1/digest/preferences", headers=env.auth(token))
        assert response.json()["last_sent_at"] is not None
        again = await digest.run_weekly_digest(env.pool, now=until + timedelta(seconds=5))
        assert again["skipped_recent"] >= 1
        assert (
            await env.admin.fetchval(
                "SELECT count(*) FROM public.notifications "
                "WHERE user_id = $1 AND type = 'weekly_digest'",
                user_id,
            )
            == 1
        )
    finally:
        await env.admin.execute("DELETE FROM public.documents WHERE id = $1", new_doc)


async def test_an_empty_week_sends_nothing(env: ApiEnv) -> None:
    org_id, user_id, _token = await env.new_org_with_owner()
    await digest.set_preferences(
        env.pool, org_id=org_id, user_id=user_id, enabled=True, email=False
    )
    summary = await digest.run_weekly_digest(env.pool)
    assert summary["considered"] >= 1
    count = await env.admin.fetchval(
        "SELECT count(*) FROM public.notifications WHERE user_id = $1 AND type = 'weekly_digest'",
        user_id,
    )
    assert count == 0, "no questions, no new evidence, no changes: nothing to say"
