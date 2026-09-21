"""Persistence for the ask flow: sessions, queries, answers (service context)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.repositories.base import PgConnection
from app.schemas.answer import AnswerResult


def reasoning_payload(result: AnswerResult) -> dict[str, Any]:
    """The structured extras the UI needs after the live stream is gone.

    Everything here is already in the SSE ``result`` event; persisting it is
    what lets the contradiction view, abstention view, evidence timeline, and
    latency footer render honestly on history/permalink/binder pages.
    """
    return {
        "contradiction": result.contradiction.model_dump(mode="json"),
        "sub_questions": result.sub_questions,
        "retrieval_grade": result.retrieval_grade,
        "rewrite_count": result.rewrite_count,
        "generation_mode": result.generation_mode,
        "escalation_banner": result.escalation_banner,
        "prompt_versions": result.prompt_versions,
        "query_type": result.query_type,
        "is_multi_hop": result.is_multi_hop,
    }


class AnswersRepository:
    async def create_session(
        self, conn: PgConnection, *, org_id: UUID, user_id: UUID, title: str | None
    ) -> UUID:
        row = await conn.fetchval(
            "INSERT INTO public.query_sessions (org_id, user_id, title) "
            "VALUES ($1, $2, $3) RETURNING id",
            org_id,
            user_id,
            title,
        )
        return UUID(str(row))

    async def create_query(
        self,
        conn: PgConnection,
        *,
        session_id: UUID,
        org_id: UUID,
        user_id: UUID,
        raw_query: str,
        mode: str = "standard",
        pico: dict[str, Any] | None = None,
        contextualized_query: str | None = None,
    ) -> UUID:
        row = await conn.fetchval(
            "INSERT INTO public.queries "
            "(session_id, org_id, user_id, raw_query, status, mode, pico, contextualized_query) "
            "VALUES ($1, $2, $3, $4, 'running', $5, $6::jsonb, $7) RETURNING id",
            session_id,
            org_id,
            user_id,
            raw_query,
            mode,
            json.dumps(pico) if pico is not None else None,
            contextualized_query,
        )
        return UUID(str(row))

    async def set_query_status(
        self,
        conn: PgConnection,
        *,
        query_id: UUID,
        org_id: UUID,
        status: str,
        query_type: str | None,
    ) -> None:
        await conn.execute(
            "UPDATE public.queries SET status = $3, query_type = $4 WHERE id = $1 AND org_id = $2",
            query_id,
            org_id,
            status,
            query_type,
        )

    async def save_answer(
        self,
        conn: PgConnection,
        *,
        query_id: UUID,
        org_id: UUID,
        result: AnswerResult,
        latency_ms: int,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        comparison_table: dict[str, Any] | None = None,
    ) -> UUID:
        """Persist the graph result to answers, with real token/cost accounting."""
        citations = [c.model_dump(mode="json") for c in result.citations]
        prompt_version = ",".join(sorted(result.prompt_versions.values())) or result.model
        row = await conn.fetchval(
            """
            INSERT INTO public.answers (
                query_id, org_id, content, citations, confidence, evidence_grade,
                has_contradiction, abstained, model, prompt_version, latency_ms,
                input_tokens, output_tokens, cost_usd, comparison_table, reasoning,
                langsmith_run_id
            ) VALUES (
                $1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                $15::jsonb, $16::jsonb, $17
            )
            RETURNING id
            """,
            query_id,
            org_id,
            result.answer,
            json.dumps(citations),
            result.confidence,
            result.evidence_grade,
            result.contradiction.detected,
            result.abstained,
            result.model,
            prompt_version,
            latency_ms,
            input_tokens,
            output_tokens,
            cost_usd,
            json.dumps(comparison_table) if comparison_table is not None else None,
            json.dumps(reasoning_payload(result)),
            result.langsmith_run_id,
        )
        return UUID(str(row))

    async def save_cached_answer(
        self,
        conn: PgConnection,
        *,
        query_id: UUID,
        org_id: UUID,
        payload: dict[str, Any],
        saved_usd: float,
    ) -> UUID:
        """Persist a cache-hit answer row: cost 0, records the saving and the flag."""
        row = await conn.fetchval(
            """
            INSERT INTO public.answers (
                query_id, org_id, content, citations, confidence, evidence_grade,
                has_contradiction, abstained, model, prompt_version, latency_ms,
                input_tokens, output_tokens, cost_usd, cached, cache_saved_usd, reasoning
            ) VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, 'cache', 'cache', 0,
                      0, 0, 0, true, $9, $10::jsonb)
            RETURNING id
            """,
            query_id,
            org_id,
            payload.get("answer", ""),
            json.dumps(payload.get("citations", [])),
            payload.get("confidence", "moderate"),
            payload.get("evidence_grade"),
            bool(payload.get("contradiction", {}).get("detected", False)),
            bool(payload.get("abstained", False)),
            saved_usd,
            json.dumps(payload.get("reasoning", {})),
        )
        return UUID(str(row))
