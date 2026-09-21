"""Living Answers: a followed answer is re-checked as the literature moves.

A clinician follows an answer; a nightly job re-runs the original question
against the current corpus and compares the result to what was delivered. When
the difference is *material* — new sources cited, confidence moved, a
contradiction appeared or resolved, or the text substantively changed — a new
immutable row lands in ``answer_versions`` and every follower is notified.

Two deliberate choices:

* The original ``answers`` row is never rewritten. What the clinician was told
  at the time is part of the clinical record; updates are new versions beside
  it, not edits over it. Version 1 is backfilled on the first supersede so the
  history is complete rather than starting at "v2".
* The re-run does **not** go through the semantic cache and does not create a
  ``queries`` row. It is a background re-check, not a user asking again — it
  must see today's corpus, and it must not distort usage analytics.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from app.config import get_settings
from app.graph.graph import AgentGraph
from app.graph.reasoner import get_reasoner
from app.repositories.base import PgConnection
from app.retrieval.embed import EmbeddingService
from app.retrieval.embedders import get_embedder
from app.retrieval.pipeline import RetrievalConfig, RetrievalPipeline
from app.retrieval.rerank import get_reranker
from app.retrieval.types import RetrievedChunk
from app.schemas.answer import AnswerResult
from app.services.webhooks import enqueue_event

if TYPE_CHECKING:
    import asyncpg
    from redis.asyncio import Redis

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("app.services.living_answers")

_WORD = re.compile(r"[a-z0-9]+")
# Below this token overlap the answer has been substantively rewritten.
SIMILARITY_FLOOR = 0.75


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if len(t) > 2}


def _similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a and not b:
        return 1.0
    union = a | b
    return round(len(a & b) / len(union), 4) if union else 0.0


def _document_ids(citations: Any) -> set[str]:
    parsed = json.loads(citations) if isinstance(citations, str) else (citations or [])
    return {str(c["document_id"]) for c in parsed if isinstance(c, dict) and c.get("document_id")}


@dataclass(slots=True)
class AnswerDiff:
    """What changed between the delivered answer and today's re-run."""

    similarity: float
    new_document_ids: list[str] = field(default_factory=list)
    dropped_document_ids: list[str] = field(default_factory=list)
    confidence_before: str | None = None
    confidence_after: str | None = None
    contradiction_before: bool = False
    contradiction_after: bool = False

    @property
    def confidence_changed(self) -> bool:
        return self.confidence_before != self.confidence_after

    @property
    def contradiction_changed(self) -> bool:
        return self.contradiction_before != self.contradiction_after

    @property
    def material(self) -> bool:
        """Is this worth interrupting a clinician for?

        New evidence, a moved confidence, an appearing/resolving contradiction,
        or a substantively rewritten answer. Cosmetic rewording is not.
        """
        return bool(
            self.new_document_ids
            or self.confidence_changed
            or self.contradiction_changed
            or self.similarity < SIMILARITY_FLOOR
        )

    def reasons(self) -> list[str]:
        out: list[str] = []
        if self.new_document_ids:
            out.append(f"{len(self.new_document_ids)} new source(s) now cited")
        if self.dropped_document_ids:
            out.append(f"{len(self.dropped_document_ids)} previously cited source(s) dropped")
        if self.confidence_changed:
            out.append(f"confidence moved from {self.confidence_before} to {self.confidence_after}")
        if self.contradiction_changed:
            out.append(
                "a contradiction appeared"
                if self.contradiction_after
                else "a previously flagged contradiction resolved"
            )
        if self.similarity < SIMILARITY_FLOOR:
            out.append(f"the answer was substantively rewritten (similarity {self.similarity})")
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "similarity": self.similarity,
            "new_document_ids": self.new_document_ids,
            "dropped_document_ids": self.dropped_document_ids,
            "confidence_before": self.confidence_before,
            "confidence_after": self.confidence_after,
            "contradiction_before": self.contradiction_before,
            "contradiction_after": self.contradiction_after,
            "reasons": self.reasons(),
        }


def diff_answers(
    *,
    old_content: str,
    old_citations: Any,
    old_confidence: str | None,
    old_contradiction: bool,
    result: AnswerResult,
) -> AnswerDiff:
    old_docs = _document_ids(old_citations)
    new_docs = {str(c.document_id) for c in result.citations}
    return AnswerDiff(
        similarity=_similarity(old_content, result.answer),
        new_document_ids=sorted(new_docs - old_docs),
        dropped_document_ids=sorted(old_docs - new_docs),
        confidence_before=old_confidence,
        confidence_after=result.confidence,
        contradiction_before=old_contradiction,
        contradiction_after=result.contradiction.detected,
    )


async def rerun_query(
    pool: DbPool, redis: Redis | None, *, org_id: UUID, query: str
) -> AnswerResult:
    """Answer the question again against today's corpus, cache-free."""
    settings = get_settings()
    cloud = settings.ai_backend == "cloud"
    embedder = EmbeddingService(get_embedder("cohere" if cloud else "local"), redis)
    retrieval = RetrievalPipeline(
        pool, embedder, get_reranker("cohere" if cloud else "local"), RetrievalConfig()
    )

    async def retrieve_fn(sub_query: str) -> list[RetrievedChunk]:
        result = await retrieval.retrieve(sub_query, org_id=org_id)
        return result.chunks

    graph = AgentGraph(retrieve_fn, get_reasoner("llm" if cloud else "heuristic"))
    return await graph.run(query, tags={"tenant_id": str(org_id), "job": "living_answers"})


async def _record_version(
    conn: PgConnection,
    *,
    answer_id: UUID,
    org_id: UUID,
    original: asyncpg.Record,
    result: AnswerResult,
    diff: AnswerDiff,
) -> int:
    """Append the new version (backfilling v1) and return its version number."""
    existing = await conn.fetchval(
        "SELECT max(version) FROM public.answer_versions WHERE answer_id = $1", answer_id
    )
    if existing is None:
        # Backfill the answer as originally delivered, so v1 is what was said.
        await conn.execute(
            """
            INSERT INTO public.answer_versions
                (answer_id, org_id, version, content, citations, confidence)
            VALUES ($1, $2, 1, $3, $4::jsonb, $5)
            ON CONFLICT (answer_id, version) DO NOTHING
            """,
            answer_id,
            org_id,
            original["content"],
            json.dumps(
                json.loads(original["citations"])
                if isinstance(original["citations"], str)
                else (original["citations"] or [])
            ),
            original["confidence"],
        )
        existing = 1
    version = int(existing) + 1
    await conn.execute(
        """
        INSERT INTO public.answer_versions (
            answer_id, org_id, version, content, citations, confidence, diff,
            superseded_by_document_ids
        ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7::jsonb, $8::uuid[])
        """,
        answer_id,
        org_id,
        version,
        result.answer,
        json.dumps([c.model_dump(mode="json") for c in result.citations]),
        result.confidence,
        json.dumps(diff.as_dict()),
        diff.new_document_ids,
    )
    return version


async def refresh_followed_answers(
    pool: DbPool, redis: Redis | None = None, *, limit: int = 200
) -> dict[str, int]:
    """Re-check every followed answer. Returns a {checked, superseded} tally.

    Runs in service context (it spans tenants), but each tenant's writes go
    through that tenant's own RLS connection.
    """
    tally = {"checked": 0, "superseded": 0, "failed": 0}
    async with pool.acquire() as conn:
        followed = await conn.fetch(
            """
            SELECT DISTINCT ON (f.answer_id)
                   f.answer_id, f.org_id,
                   a.content, a.citations, a.confidence, a.has_contradiction,
                   coalesce(q.contextualized_query, q.raw_query) AS query
            FROM public.followed_answers f
            JOIN public.answers a ON a.id = f.answer_id
            JOIN public.queries q ON q.id = a.query_id
            ORDER BY f.answer_id
            LIMIT $1
            """,
            limit,
        )

    for row in followed:
        answer_id, org_id = row["answer_id"], row["org_id"]
        tally["checked"] += 1
        try:
            result = await rerun_query(pool, redis, org_id=org_id, query=row["query"])
        except Exception as exc:
            tally["failed"] += 1
            logger.error(
                "living_answer_rerun_failed",
                answer_id=str(answer_id),
                error=f"{type(exc).__name__}: {exc}",
            )
            continue

        diff = diff_answers(
            old_content=row["content"],
            old_citations=row["citations"],
            old_confidence=row["confidence"],
            old_contradiction=bool(row["has_contradiction"]),
            result=result,
        )
        if not diff.material:
            continue
        # An abstention is not an "update" worth alarming a clinician with —
        # it means today's retrieval was thin, not that the guidance changed.
        if result.abstained:
            logger.info("living_answer_rerun_abstained", answer_id=str(answer_id))
            continue

        tally["superseded"] += 1
        # Service context, deliberately. 006 REVOKEs INSERT on answer_versions
        # from `authenticated` and reserves notifications for the service role:
        # a version record and the alert about it are system statements about
        # the evidence, and a tenant must never be able to forge either. The
        # org predicate on every write is what scopes them.
        async with pool.acquire() as conn, conn.transaction():
            followers = await conn.fetch(
                "SELECT user_id FROM public.followed_answers WHERE answer_id = $1", answer_id
            )
            version = await _record_version(
                conn,
                answer_id=answer_id,
                org_id=org_id,
                original=row,
                result=result,
                diff=diff,
            )
            for follower in followers:
                await conn.execute(
                    "INSERT INTO public.notifications (org_id, user_id, type, payload) "
                    "VALUES ($1, $2, 'answer_updated', $3::jsonb)",
                    org_id,
                    follower["user_id"],
                    json.dumps(
                        {
                            "answer_id": str(answer_id),
                            "version": version,
                            "reasons": diff.reasons(),
                        }
                    ),
                )
            await enqueue_event(
                conn,
                org_id=org_id,
                event="answer.superseded",
                payload={
                    "answer_id": str(answer_id),
                    "version": version,
                    "diff": diff.as_dict(),
                },
            )
        logger.info(
            "living_answer_superseded",
            answer_id=str(answer_id),
            version=version,
            reasons=diff.reasons(),
        )
    return tally
