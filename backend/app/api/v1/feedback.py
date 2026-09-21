"""Feedback on an answer — the signal that drives evaluation and improvement.

One vote per (answer, user): re-submitting updates the existing row rather
than stacking duplicates. The ``reason`` taxonomy is deliberately about the
answer's *failure mode*, including the one that matters most for a system
built to abstain: it should have abstained.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import DbPool, get_asyncpg_pool
from app.core.errors import NotFoundError
from app.core.ratelimit import enforce_rate_limit
from app.core.security import CurrentUser, require_role
from app.graph.tracing import attach_scores
from app.repositories.base import tenant_connection
from app.schemas.query import FeedbackOut, FeedbackRequest, ReviewQueueItem, ReviewQueueOut

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("", status_code=201)
async def submit_feedback(
    body: FeedbackRequest,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> FeedbackOut:
    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        # RLS already confines this to the tenant; the explicit check turns a
        # foreign/unknown answer id into a clean 404 instead of an FK error.
        exists = await conn.fetchval("SELECT 1 FROM public.answers WHERE id = $1", body.answer_id)
        if exists is None:
            raise NotFoundError("answer not found")
        run_id = await conn.fetchval(
            "SELECT langsmith_run_id FROM public.answers WHERE id = $1", body.answer_id
        )
        row = await conn.fetchrow(
            """
            INSERT INTO public.feedback (answer_id, org_id, user_id, rating, reason, comment)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (answer_id, user_id) DO UPDATE
                SET rating = excluded.rating,
                    reason = excluded.reason,
                    comment = excluded.comment
            RETURNING id, answer_id, rating, reason, comment, created_at
            """,
            body.answer_id,
            user.org_id,
            user.user_id,
            body.rating,
            body.reason,
            body.comment,
        )
    assert row is not None  # INSERT .. RETURNING
    # The same verdict lands on the traced run (no-op without LangSmith).
    await attach_scores(
        run_id,
        {"thumbs_up": body.rating == "up"},
        comment=f"{body.reason or ''} {body.comment or ''}".strip() or None,
    )
    return FeedbackOut(
        id=row["id"],
        answer_id=row["answer_id"],
        rating=row["rating"],
        reason=row["reason"],
        comment=row["comment"],
        created_at=row["created_at"],
    )


@router.get("/review-queue")
async def review_queue(
    user: Annotated[CurrentUser, Depends(require_role("owner"))],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    status: str | None = None,
    limit: int = 50,
) -> ReviewQueueOut:
    """Answers this org flagged as wrong or unsupported, and what became of
    them: pending review, promoted into the golden set, or rejected. The
    review itself happens with ``python -m evals.golden.promote``."""
    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        rows = await conn.fetch(
            """
            SELECT r.id, r.feedback_id, r.answer_id, a.query_id, r.status::text AS status,
                   r.golden_id,
                   r.created_at, r.reviewed_at,
                   q.raw_query AS question, left(a.content, 280) AS answer_excerpt,
                   a.confidence::text AS confidence,
                   jsonb_array_length(a.citations) AS citations,
                   f.reason::text AS reason, f.comment
            FROM public.golden_reviews r
            JOIN public.feedback f ON f.id = r.feedback_id
            JOIN public.answers a ON a.id = r.answer_id
            JOIN public.queries q ON q.id = a.query_id
            WHERE ($1::text IS NULL OR r.status::text = $1)
            ORDER BY r.created_at DESC
            LIMIT $2
            """,
            status,
            max(1, min(limit, 200)),
        )
        counts = await conn.fetch(
            "SELECT status::text AS status, count(*) AS n FROM public.golden_reviews GROUP BY 1"
        )
    by_status = {str(r["status"]): int(r["n"]) for r in counts}
    return ReviewQueueOut(
        items=[
            ReviewQueueItem(
                id=r["id"],
                feedback_id=r["feedback_id"],
                answer_id=r["answer_id"],
                query_id=r["query_id"],
                status=r["status"],
                question=r["question"],
                answer_excerpt=r["answer_excerpt"],
                reason=r["reason"],
                comment=r["comment"],
                confidence=r["confidence"],
                citations=int(r["citations"]),
                golden_id=r["golden_id"],
                created_at=r["created_at"],
                reviewed_at=r["reviewed_at"],
            )
            for r in rows
        ],
        pending=by_status.get("pending", 0),
        promoted=by_status.get("promoted", 0),
        rejected=by_status.get("rejected", 0),
    )
