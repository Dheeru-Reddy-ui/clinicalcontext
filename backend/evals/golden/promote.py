"""The feedback → golden-set loop: review a flagged answer and promote it.

A thumbs-down with reason ``wrong`` or ``unsupported`` lands in
``golden_reviews`` (migration 020). A reviewer reads the case, writes the
reference answer a clinician would accept, names the passages that support
it, and promotes it in one command; the item joins ``set.jsonl`` with
``provenance.source = "feedback"`` and survives rebuilds of the corpus-derived
items. Rejecting records why the case was not a good test.

    uv run python -m evals.golden.promote list
    uv run python -m evals.golden.promote show <review-id>
    uv run python -m evals.golden.promote promote <review-id> \\
        --reviewer "Dr A. Reddy" --category therapy --grade A \\
        --reference-answer-file answer.md [--chunk <uuid> ...] [--abstain] [--note "..."]
    uv run python -m evals.golden.promote reject <review-id> --reviewer "..." --note "..."

Promotion needs the service database connection (reviews are cross-tenant
operator work). The clinician's raw question is the golden question; the
reviewer's answer is the reference; ``--chunk`` names the gold passages
(default: the passages the reviewed answer cited, which the reviewer is
confirming are the right ones).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import asyncpg

from app.config import get_settings
from app.core.deps import DbPool
from evals.golden.schema import (
    SET_PATH,
    Category,
    GoldenItem,
    Grade,
    Provenance,
    append_item,
    load_set,
)

_CATEGORIES = ("therapy", "harm", "first_line", "guideline", "diagnosis", "prognosis", "comparison")


async def _pool() -> DbPool:
    return await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=2)


async def fetch_review(pool: DbPool, review_id: UUID) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT r.id, r.feedback_id, r.answer_id, r.org_id, r.status::text AS status,
                   r.golden_id, r.reviewer, r.note, r.created_at, r.reviewed_at,
                   q.raw_query AS question, a.content AS answer, a.citations,
                   a.confidence::text AS confidence, a.evidence_grade::text AS evidence_grade,
                   a.abstained, f.reason::text AS reason, f.comment
            FROM public.golden_reviews r
            JOIN public.feedback f ON f.id = r.feedback_id
            JOIN public.answers a ON a.id = r.answer_id
            JOIN public.queries q ON q.id = a.query_id
            WHERE r.id = $1
            """,
            review_id,
        )
    return None if row is None else dict(row)


async def list_reviews(pool: DbPool, status: str | None) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.id, r.status::text AS status, r.created_at, r.golden_id,
                   q.raw_query AS question, f.reason::text AS reason
            FROM public.golden_reviews r
            JOIN public.feedback f ON f.id = r.feedback_id
            JOIN public.answers a ON a.id = r.answer_id
            JOIN public.queries q ON q.id = a.query_id
            WHERE ($1::text IS NULL OR r.status::text = $1)
            ORDER BY r.created_at
            """,
            status,
        )
    return [dict(r) for r in rows]


def _next_id(items: list[GoldenItem]) -> str:
    n = sum(1 for i in items if i.provenance.source == "feedback")
    return f"feedback-{n + 1:03d}"


async def _document_ids(pool: DbPool, chunk_ids: list[str]) -> list[str]:
    if not chunk_ids:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT document_id::text AS d FROM public.chunks WHERE id = ANY($1::uuid[])",
            [UUID(c) for c in chunk_ids],
        )
    return [str(r["d"]) for r in rows]


async def promote(
    pool: DbPool,
    review_id: UUID,
    *,
    reviewer: str,
    category: str,
    reference_answer: str,
    grade: str | None,
    chunk_ids: list[str] | None,
    abstain: bool,
    contradiction: bool,
    note: str | None,
    set_path: Path,
) -> GoldenItem:
    review = await fetch_review(pool, review_id)
    if review is None:
        raise SystemExit(f"no review {review_id}")
    if review["status"] != "pending":
        raise SystemExit(f"review {review_id} is already {review['status']}")
    if len(reference_answer.strip()) < 40:
        raise SystemExit("the reference answer must be a real answer (at least 40 characters)")
    citations = (
        json.loads(review["citations"])
        if isinstance(review["citations"], str)
        else review["citations"]
    )
    cited = [str(c.get("chunk_id")) for c in citations if c.get("chunk_id")]
    gold_chunks = [] if abstain else (chunk_ids if chunk_ids else cited)
    if not abstain and not gold_chunks:
        raise SystemExit("name the supporting passages with --chunk (the answer cited none)")
    items = load_set(set_path)
    item = GoldenItem(
        id=_next_id(items),
        question=str(review["question"]).strip(),
        category=cast(Category, category),
        pattern="reviewed clinician question (from feedback)",
        reference_answer=reference_answer.strip(),
        relevant_document_ids=await _document_ids(pool, gold_chunks),
        relevant_chunk_ids=gold_chunks,
        expected_grade=cast(Grade, grade) if grade else None,
        contradiction_expected=contradiction,
        expected_abstain=abstain,
        provenance=Provenance(
            source="feedback",
            built_at=datetime.now(UTC).isoformat(),
            feedback_id=str(review["feedback_id"]),
            reviewed_by=reviewer,
            review_note=note,
        ),
    )
    append_item(item, set_path)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.golden_reviews SET status = 'promoted', golden_id = $2, reviewer = $3, "
            "note = $4, reviewed_at = now() WHERE id = $1",
            review_id,
            item.id,
            reviewer,
            note,
        )
    return item


async def reject(pool: DbPool, review_id: UUID, *, reviewer: str, note: str) -> None:
    async with pool.acquire() as conn:
        updated = await conn.execute(
            "UPDATE public.golden_reviews SET status = 'rejected', reviewer = $2, note = $3, "
            "reviewed_at = now() WHERE id = $1 AND status = 'pending'",
            review_id,
            reviewer,
            note,
        )
    if updated != "UPDATE 1":
        raise SystemExit(f"review {review_id} is not pending")


async def _main(args: argparse.Namespace) -> int:
    pool = await _pool()
    try:
        if args.command == "list":
            for r in await list_reviews(pool, args.status):
                print(
                    f"{r['id']}  {r['status']:9s}  {r['created_at']:%Y-%m-%d}  "
                    f"[{r['reason']}] {r['question'][:80]}"
                    + (f"  → {r['golden_id']}" if r["golden_id"] else "")
                )
            return 0
        if args.command == "show":
            review = await fetch_review(pool, args.review_id)
            if review is None:
                raise SystemExit("not found")
            print(json.dumps(review, indent=2, default=str))
            return 0
        if args.command == "reject":
            await reject(pool, args.review_id, reviewer=args.reviewer, note=args.note)
            print(f"rejected {args.review_id}")
            return 0
        reference = (
            args.reference_answer_file.read_text(encoding="utf-8")
            if args.reference_answer_file
            else args.reference_answer or ""
        )
        item = await promote(
            pool,
            args.review_id,
            reviewer=args.reviewer,
            category=args.category,
            reference_answer=reference,
            grade=args.grade,
            chunk_ids=args.chunk,
            abstain=args.abstain,
            contradiction=args.contradiction,
            note=args.note,
            set_path=args.set,
        )
        print(f"promoted {args.review_id} as {item.id}: {item.question}")
        return 0
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="reviews, pending first")
    listing.add_argument("--status", choices=["pending", "promoted", "rejected"])

    show = sub.add_parser("show", help="the full case: question, answer, citations, feedback")
    show.add_argument("review_id", type=UUID)

    prom = sub.add_parser("promote", help="add the reviewed case to the golden set")
    prom.add_argument("review_id", type=UUID)
    prom.add_argument("--reviewer", required=True)
    prom.add_argument("--category", required=True, choices=_CATEGORIES)
    prom.add_argument("--reference-answer")
    prom.add_argument("--reference-answer-file", type=Path)
    prom.add_argument("--grade", choices=["A", "B", "C", "D"])
    prom.add_argument("--chunk", action="append", help="gold passage chunk id (repeatable)")
    prom.add_argument("--abstain", action="store_true", help="the right answer is to abstain")
    prom.add_argument("--contradiction", action="store_true")
    prom.add_argument("--note")
    prom.add_argument("--set", type=Path, default=SET_PATH)

    rej = sub.add_parser("reject", help="record that the case is not a good test")
    rej.add_argument("review_id", type=UUID)
    rej.add_argument("--reviewer", required=True)
    rej.add_argument("--note", required=True)

    args = parser.parse_args(argv)
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["fetch_review", "list_reviews", "promote", "reject"]
