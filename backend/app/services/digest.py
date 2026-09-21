"""The weekly evidence digest (Phase 13), on the Phase 9 freshness pipeline.

Once a week, each user who opted in gets one notification (and, if they also
opted in to email, one message) with three things, all computed from rows
the system already writes:

1. **New evidence in their topics.** A user's topics are the MeSH headings
   of the documents their answers cited in the last 90 days (minus the check
   tags — "Humans", "Female", "Aged" — which describe a population, not a
   topic); the digest lists
   documents ingested this week that carry those headings, best evidence
   first. No topic list is maintained by hand and nothing is inferred beyond
   what the user actually asked about.
2. **Living Answer changes.** New versions written this week for answers the
   user asked or follows (the Phase 9 re-check job writes those).
3. **Their organisation's week.** Questions asked, abstentions, contradictions
   surfaced — the same numbers as the dashboard, for the window.

A digest with nothing in it is not sent; the job records that it looked.
"""

from __future__ import annotations

import asyncio
import json
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any
from uuid import UUID

import structlog

from app.config import Settings, get_settings
from app.core.deps import DbPool
from app.repositories.base import tenant_connection
from app.services.suggest import CHECK_TAGS

logger = structlog.stdlib.get_logger("app.services.digest")

NOTIFICATION_TYPE = "weekly_digest"
TOPIC_LOOKBACK_DAYS = 90
MAX_TOPICS = 12
MAX_NEW_DOCUMENTS = 15
MAX_CHANGES = 10
# A digest is weekly: a user who was sent one less than this long ago is
# skipped, whatever the schedule that invoked the job.
MIN_INTERVAL = timedelta(days=6)


@dataclass(slots=True)
class DigestPreferences:
    user_id: UUID
    org_id: UUID
    enabled: bool
    email: bool
    last_sent_at: datetime | None


# -- preferences -------------------------------------------------------------------


async def get_preferences(pool: DbPool, *, org_id: UUID, user_id: UUID) -> DigestPreferences:
    async with tenant_connection(pool, org_id, user_id) as conn:
        row = await conn.fetchrow(
            "SELECT enabled, email, last_sent_at FROM public.digest_preferences WHERE user_id = $1",
            user_id,
        )
    if row is None:
        return DigestPreferences(user_id, org_id, enabled=False, email=False, last_sent_at=None)
    return DigestPreferences(user_id, org_id, row["enabled"], row["email"], row["last_sent_at"])


async def set_preferences(
    pool: DbPool,
    *,
    org_id: UUID,
    user_id: UUID,
    enabled: bool | None,
    email: bool | None,
) -> DigestPreferences:
    current = await get_preferences(pool, org_id=org_id, user_id=user_id)
    new_enabled = current.enabled if enabled is None else enabled
    # Email is a copy of the in-app digest: it cannot be on when the digest is off.
    new_email = (current.email if email is None else email) and new_enabled
    async with tenant_connection(pool, org_id, user_id) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.digest_preferences (user_id, org_id, enabled, email)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO UPDATE
                SET enabled = EXCLUDED.enabled, email = EXCLUDED.email, updated_at = now()
            RETURNING enabled, email, last_sent_at
            """,
            user_id,
            org_id,
            new_enabled,
            new_email,
        )
    assert row is not None  # INSERT .. RETURNING
    return DigestPreferences(user_id, org_id, row["enabled"], row["email"], row["last_sent_at"])


# -- building ----------------------------------------------------------------------


async def build_digest(
    pool: DbPool, *, org_id: UUID, user_id: UUID, since: datetime, until: datetime
) -> dict[str, Any]:
    """The digest payload for one user over [since, until)."""
    async with tenant_connection(pool, org_id, user_id) as conn:
        topics = await conn.fetch(
            """
            SELECT mesh AS topic, count(*) AS n
            FROM public.answers a
            JOIN public.queries q ON q.id = a.query_id
            CROSS JOIN LATERAL jsonb_array_elements(a.citations) AS c
            JOIN public.documents d ON d.id = (c ->> 'document_id')::uuid
            CROSS JOIN LATERAL jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(d.metadata -> 'mesh_terms') = 'array'
                     THEN d.metadata -> 'mesh_terms' ELSE '[]'::jsonb END
            ) AS mesh
            WHERE q.user_id = $1
              AND a.created_at >= $2::timestamptz - make_interval(days => $3)
              AND lower(mesh) <> ALL($5::text[])
            GROUP BY mesh
            ORDER BY n DESC, mesh
            LIMIT $4
            """,
            user_id,
            until,
            TOPIC_LOOKBACK_DAYS,
            MAX_TOPICS,
            sorted(CHECK_TAGS),
        )
        topic_names = [r["topic"] for r in topics]
        new_documents = (
            await conn.fetch(
                """
                SELECT d.id, d.title, d.journal, d.publication_date, d.pmid, d.url,
                       d.evidence_grade::text AS evidence_grade, d.study_type::text AS study_type,
                       ARRAY(
                           SELECT t FROM jsonb_array_elements_text(d.metadata -> 'mesh_terms') AS t
                           WHERE t = ANY($1::text[])
                       ) AS topics
                FROM public.documents d
                WHERE d.ingested_at >= $2 AND d.ingested_at < $3
                  AND (d.org_id IS NULL OR d.org_id = $4)
                  AND jsonb_typeof(d.metadata -> 'mesh_terms') = 'array'
                  AND d.metadata -> 'mesh_terms' ?| $1::text[]
                ORDER BY d.evidence_grade NULLS LAST, d.publication_date DESC NULLS LAST
                LIMIT $5
                """,
                topic_names,
                since,
                until,
                org_id,
                MAX_NEW_DOCUMENTS,
            )
            if topic_names
            else []
        )
        changes = await conn.fetch(
            """
            SELECT v.answer_id, v.version, v.created_at, v.diff, q.raw_query AS query
            FROM public.answer_versions v
            JOIN public.answers a ON a.id = v.answer_id
            JOIN public.queries q ON q.id = a.query_id
            WHERE v.created_at >= $2 AND v.created_at < $3
              AND (q.user_id = $1 OR EXISTS (
                    SELECT 1 FROM public.followed_answers f
                    WHERE f.answer_id = v.answer_id AND f.user_id = $1))
            ORDER BY v.created_at DESC
            LIMIT $4
            """,
            user_id,
            since,
            until,
            MAX_CHANGES,
        )
        usage = await conn.fetchrow(
            """
            WITH latest AS (
                SELECT q.id, q.status::text AS status, a.abstained, a.has_contradiction
                FROM public.queries q
                LEFT JOIN LATERAL (
                    SELECT abstained, has_contradiction FROM public.answers
                    WHERE query_id = q.id ORDER BY created_at DESC LIMIT 1
                ) a ON true
                WHERE q.org_id = $1 AND q.created_at >= $2 AND q.created_at < $3
            )
            SELECT count(*) AS queries,
                   count(*) FILTER (WHERE abstained) AS abstained,
                   count(*) FILTER (WHERE has_contradiction) AS contradictions,
                   count(*) FILTER (WHERE status = 'blocked') AS blocked
            FROM latest
            """,
            org_id,
            since,
            until,
        )
    assert usage is not None
    return {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "topics": topic_names,
        "new_documents": [
            {
                "document_id": str(r["id"]),
                "title": r["title"],
                "journal": r["journal"],
                "publication_date": r["publication_date"].isoformat()
                if r["publication_date"]
                else None,
                "pmid": r["pmid"],
                "url": r["url"],
                "evidence_grade": r["evidence_grade"],
                "study_type": r["study_type"],
                "topics": list(r["topics"] or []),
            }
            for r in new_documents
        ],
        "answer_changes": [
            {
                "answer_id": str(r["answer_id"]),
                "version": int(r["version"]),
                "query": r["query"],
                "changed_at": r["created_at"].isoformat(),
                "reasons": _reasons(r["diff"]),
            }
            for r in changes
        ],
        "usage": {
            "queries": int(usage["queries"] or 0),
            "abstained": int(usage["abstained"] or 0),
            "contradictions": int(usage["contradictions"] or 0),
            "blocked": int(usage["blocked"] or 0),
        },
    }


def _reasons(diff: Any) -> list[str]:
    payload = json.loads(diff) if isinstance(diff, str) else (diff or {})
    reasons = payload.get("reasons") if isinstance(payload, dict) else None
    return [str(r) for r in reasons] if isinstance(reasons, list) else []


def is_empty(digest: dict[str, Any]) -> bool:
    return (
        not digest["new_documents"]
        and not digest["answer_changes"]
        and digest["usage"]["queries"] == 0
    )


# -- sending -----------------------------------------------------------------------


def render_text(digest: dict[str, Any], *, app_url: str) -> str:
    """The email body: plain text, the same content as the notification."""
    lines = [
        "Your weekly evidence digest",
        f"{digest['since'][:10]} to {digest['until'][:10]}",
        "",
    ]
    if digest["topics"]:
        lines += ["Topics (from what you asked): " + ", ".join(digest["topics"]), ""]
    lines.append(f"New evidence in your topics ({len(digest['new_documents'])})")
    for d in digest["new_documents"] or []:
        grade = f" [grade {d['evidence_grade']}]" if d.get("evidence_grade") else ""
        where = " · ".join(
            x for x in (d.get("journal"), (d.get("publication_date") or "")[:4]) if x
        )
        lines.append(f"  - {d['title']}{grade}")
        if where:
            lines.append(f"    {where}")
        if d.get("url"):
            lines.append(f"    {d['url']}")
    if not digest["new_documents"]:
        lines.append("  nothing new this week")
    lines += ["", f"Living Answer changes ({len(digest['answer_changes'])})"]
    for c in digest["answer_changes"] or []:
        lines.append(f"  - v{c['version']}: {c['query']}")
        if c["reasons"]:
            lines.append("    " + "; ".join(c["reasons"]))
        lines.append(f"    {app_url}/app/answers/{c['answer_id']}/versions")
    if not digest["answer_changes"]:
        lines.append("  no followed answer changed")
    u = digest["usage"]
    lines += [
        "",
        "Your organisation this week",
        f"  {u['queries']} questions · {u['abstained']} abstentions · "
        f"{u['contradictions']} with contradictions · {u['blocked']} blocked by guardrails",
        "",
        f"Manage this digest: {app_url}/app/notifications",
    ]
    return "\n".join(lines)


def _send_email_sync(settings: Settings, *, to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        smtp.send_message(message)


async def send_email(settings: Settings, *, to: str, subject: str, body: str) -> bool:
    """Deliver through the configured SMTP relay; False when none is configured
    or delivery failed (logged) — the in-app digest is never held up by email."""
    if not settings.smtp_host:
        return False
    try:
        await asyncio.to_thread(_send_email_sync, settings, to=to, subject=subject, body=body)
    except Exception as exc:
        logger.warning("digest_email_failed", error=f"{type(exc).__name__}: {exc}")
        return False
    return True


async def deliver(
    pool: DbPool,
    *,
    prefs: DigestPreferences,
    digest: dict[str, Any],
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Write the in-app notification, send the email copy when opted in, and
    stamp ``last_sent_at``."""
    resolved = settings or get_settings()
    emailed = False
    email_to = None
    # Notifications are system-written (users only read them), and the
    # address lives with the identity provider: both as the service, like the
    # Living Answers job, not under the user's RLS context.
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.notifications (org_id, user_id, type, payload) "
            "VALUES ($1, $2, $3, $4::jsonb)",
            prefs.org_id,
            prefs.user_id,
            NOTIFICATION_TYPE,
            json.dumps(digest),
        )
        if prefs.email:
            email_to = await conn.fetchval(
                "SELECT email FROM auth.users WHERE id = $1", prefs.user_id
            )
    if email_to:
        emailed = await send_email(
            resolved,
            to=email_to,
            subject=f"ClinicalContext weekly digest · {digest['until'][:10]}",
            body=render_text(digest, app_url=resolved.app_public_url),
        )
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.digest_preferences SET last_sent_at = $2 WHERE user_id = $1",
            prefs.user_id,
            datetime.fromisoformat(digest["until"]),
        )
    return {"in_app": True, "email": emailed}


# -- the job -----------------------------------------------------------------------


async def run_weekly_digest(
    pool: DbPool, *, now: datetime | None = None, force: bool = False
) -> dict[str, int]:
    """Send this week's digest to every user who opted in and is due."""
    until = now or datetime.now(UTC)
    summary = {"considered": 0, "sent": 0, "emailed": 0, "skipped_recent": 0, "skipped_empty": 0}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id, org_id, enabled, email, last_sent_at "
            "FROM public.digest_preferences WHERE enabled ORDER BY org_id, user_id"
        )
    for r in rows:
        prefs = DigestPreferences(
            r["user_id"], r["org_id"], r["enabled"], r["email"], r["last_sent_at"]
        )
        summary["considered"] += 1
        if not force and prefs.last_sent_at and until - prefs.last_sent_at < MIN_INTERVAL:
            summary["skipped_recent"] += 1
            continue
        since = prefs.last_sent_at or until - timedelta(days=7)
        digest = await build_digest(
            pool, org_id=prefs.org_id, user_id=prefs.user_id, since=since, until=until
        )
        if is_empty(digest):
            summary["skipped_empty"] += 1
            continue
        outcome = await deliver(pool, prefs=prefs, digest=digest)
        summary["sent"] += 1
        summary["emailed"] += int(outcome["email"])
        logger.info(
            "weekly_digest_sent",
            user_id=str(prefs.user_id),
            new_documents=len(digest["new_documents"]),
            changes=len(digest["answer_changes"]),
            emailed=outcome["email"],
        )
    return summary
