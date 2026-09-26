"""The tutor's practice record: what each learner answered, and how they are
doing — overall, this week, topic by topic, the topics to revisit, and the
questions they got wrong. Every query runs as the learner (tenant
connection), and migration 027's policy lets each person see only their own
rows.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from app.repositories.base import PgConnection
from app.schemas.learn import AttemptIn

TOPICS = 12
WEAKEST = 5
MISTAKES = 10
# A topic is one to revisit after this many answers at below this accuracy.
_WEAK_MIN_ANSWERED = 3
_WEAK_BELOW = 0.7


async def record_attempt(
    conn: PgConnection, *, org_id: UUID, user_id: UUID, attempt: AttemptIn
) -> UUID:
    value = await conn.fetchval(
        """
        INSERT INTO public.tutor_attempts
            (user_id, org_id, mode, level, specialty, topic, question, chosen, answer,
             explanation, correct)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        RETURNING id
        """,
        user_id,
        org_id,
        attempt.mode,
        attempt.level,
        attempt.specialty,
        " ".join(attempt.topic.split()),
        attempt.question,
        attempt.chosen,
        attempt.answer,
        attempt.explanation,
        attempt.correct,
    )
    return UUID(str(value))


def streak(days: list[date], today: date) -> int:
    """Consecutive days with practice, ending today (or yesterday, so a streak
    is not lost before today's practice)."""
    have = set(days)
    start = today if today in have else today - timedelta(days=1)
    count = 0
    while start - timedelta(days=count) in have:
        count += 1
    return count


def _accuracy(correct: int, answered: int) -> float:
    return round(correct / answered, 3) if answered else 0.0


async def progress(conn: PgConnection, *, user_id: UUID) -> dict[str, Any]:
    totals = await conn.fetchrow(
        """
        SELECT count(*) AS answered,
               count(*) FILTER (WHERE correct) AS correct,
               count(*) FILTER (WHERE created_at > now() - interval '7 days') AS week_answered,
               count(*) FILTER (WHERE correct AND created_at > now() - interval '7 days')
                   AS week_correct
        FROM public.tutor_attempts WHERE user_id = $1
        """,
        user_id,
    )
    assert totals is not None
    topic_rows = await conn.fetch(
        """
        SELECT min(topic) AS topic, max(specialty) AS specialty, count(*) AS answered,
               count(*) FILTER (WHERE correct) AS correct, max(created_at) AS last_at
        FROM public.tutor_attempts WHERE user_id = $1
        GROUP BY lower(topic)
        ORDER BY max(created_at) DESC
        LIMIT 60
        """,
        user_id,
    )
    topics = [
        {
            "topic": r["topic"],
            "specialty": r["specialty"],
            "answered": int(r["answered"]),
            "correct": int(r["correct"]),
            "accuracy": _accuracy(int(r["correct"]), int(r["answered"])),
            "last_at": r["last_at"],
        }
        for r in topic_rows
    ]
    weakest = sorted(
        (t for t in topics if t["answered"] >= _WEAK_MIN_ANSWERED and t["accuracy"] < _WEAK_BELOW),
        key=lambda t: (t["accuracy"], -t["answered"]),
    )[:WEAKEST]
    mistakes = await conn.fetch(
        """
        SELECT topic, question, chosen, answer, explanation, created_at
        FROM public.tutor_attempts
        WHERE user_id = $1 AND NOT correct
        ORDER BY created_at DESC
        LIMIT $2
        """,
        user_id,
        MISTAKES,
    )
    days = await conn.fetch(
        """
        SELECT DISTINCT (created_at AT TIME ZONE 'UTC')::date AS day
        FROM public.tutor_attempts
        WHERE user_id = $1 AND created_at > now() - interval '120 days'
        """,
        user_id,
    )
    answered, correct = int(totals["answered"]), int(totals["correct"])
    return {
        "answered": answered,
        "correct": correct,
        "accuracy": _accuracy(correct, answered) if answered else None,
        "week_answered": int(totals["week_answered"]),
        "week_correct": int(totals["week_correct"]),
        "streak_days": streak([r["day"] for r in days], datetime.now(UTC).date()),
        "topics": topics[:TOPICS],
        "weakest": weakest,
        "mistakes": [dict(r) for r in mistakes],
    }


async def clear(conn: PgConnection, *, user_id: UUID) -> int:
    status = await conn.execute("DELETE FROM public.tutor_attempts WHERE user_id = $1", user_id)
    return int(status.split()[-1]) if status.startswith("DELETE") else 0
