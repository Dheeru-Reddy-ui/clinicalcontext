"""Read access for query detail and history (tenant-scoped via RLS)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.errors import NotFoundError
from app.repositories.base import tenant_connection

if TYPE_CHECKING:
    import asyncpg

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class QueryReadRepository:
    def __init__(self, pool: DbPool) -> None:
        self._pool = pool

    async def get_detail(self, *, org_id: UUID, user_id: UUID, query_id: UUID) -> dict[str, Any]:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            query = await conn.fetchrow(
                "SELECT id, raw_query, contextualized_query, query_type, mode, pico, status, "
                "guardrail_verdict, created_at FROM public.queries WHERE id = $1",
                query_id,
            )
            if query is None:
                raise NotFoundError("query not found")
            answer = await conn.fetchrow(
                "SELECT id, content, citations, confidence, evidence_grade, has_contradiction, "
                "abstained, model, prompt_version, latency_ms, input_tokens, output_tokens, "
                "cost_usd, cached, "
                "cache_saved_usd, comparison_table, reasoning, created_at "
                "FROM public.answers WHERE query_id = $1 ORDER BY created_at DESC LIMIT 1",
                query_id,
            )
            traces = await conn.fetch(
                "SELECT stage, chunk_ids, scores, duration_ms FROM public.retrieval_traces "
                "WHERE query_id = $1 ORDER BY created_at",
                query_id,
            )
        return {
            "query": {
                "id": str(query["id"]),
                "raw_query": query["raw_query"],
                "contextualized_query": query["contextualized_query"],
                "query_type": query["query_type"],
                "mode": query["mode"],
                "pico": _json(query["pico"]),
                "status": query["status"],
                "created_at": query["created_at"].isoformat(),
            },
            "guardrail_verdict": _json(query["guardrail_verdict"]),
            "answer": _answer_dict(answer) if answer else None,
            "retrieval_traces": [
                {
                    "stage": t["stage"],
                    "chunk_ids": [str(c) for c in t["chunk_ids"]],
                    "scores": list(t["scores"]),
                    "duration_ms": t["duration_ms"],
                }
                for t in traces
            ],
        }

    async def list_history(
        self,
        *,
        org_id: UUID,
        user_id: UUID,
        limit: int,
        offset: int,
        status: str | None = None,
        has_contradiction: bool | None = None,
        abstained: bool | None = None,
        only_user_id: UUID | None = None,
    ) -> dict[str, Any]:
        clauses = ["q.org_id = $1"]
        params: list[Any] = [org_id]
        if status is not None:
            params.append(status)
            clauses.append(f"q.status = ${len(params)}")
        if only_user_id is not None:
            params.append(only_user_id)
            clauses.append(f"q.user_id = ${len(params)}")
        if has_contradiction is not None:
            params.append(has_contradiction)
            clauses.append(f"a.has_contradiction = ${len(params)}")
        if abstained is not None:
            params.append(abstained)
            clauses.append(f"a.abstained = ${len(params)}")
        where = " AND ".join(clauses)

        # LATERAL join to the latest answer per query for filtering + summary.
        base = f"""
            FROM public.queries q
            LEFT JOIN LATERAL (
                SELECT confidence, abstained, has_contradiction, cost_usd, latency_ms, cached
                FROM public.answers WHERE query_id = q.id ORDER BY created_at DESC LIMIT 1
            ) a ON true
            WHERE {where}
        """
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            total = await conn.fetchval(f"SELECT count(*) {base}", *params)
            rows = await conn.fetch(
                f"""
                SELECT q.id, q.raw_query, q.query_type, q.mode, q.status, q.created_at,
                       a.confidence, a.abstained, a.has_contradiction, a.cost_usd, a.cached
                {base}
                ORDER BY q.created_at DESC
                LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
                """,
                *params,
                limit,
                offset,
            )
        return {
            "total": int(total or 0),
            "limit": limit,
            "offset": offset,
            "items": [
                {
                    "id": str(r["id"]),
                    "raw_query": r["raw_query"],
                    "query_type": r["query_type"],
                    "mode": r["mode"],
                    "status": r["status"],
                    "created_at": r["created_at"].isoformat(),
                    "confidence": r["confidence"],
                    "abstained": r["abstained"],
                    "has_contradiction": r["has_contradiction"],
                    "cost_usd": float(r["cost_usd"]) if r["cost_usd"] is not None else None,
                    "cached": r["cached"],
                }
                for r in rows
            ],
        }


def _answer_dict(answer: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": str(answer["id"]),
        "content": answer["content"],
        "citations": _json(answer["citations"]),
        "confidence": answer["confidence"],
        "evidence_grade": answer["evidence_grade"],
        "has_contradiction": answer["has_contradiction"],
        "abstained": answer["abstained"],
        "model": answer["model"],
        "prompt_version": answer["prompt_version"],
        "latency_ms": answer["latency_ms"],
        "input_tokens": answer["input_tokens"],
        "output_tokens": answer["output_tokens"],
        "cost_usd": float(answer["cost_usd"]) if answer["cost_usd"] is not None else None,
        "cached": answer["cached"],
        "cache_saved_usd": (
            float(answer["cache_saved_usd"]) if answer["cache_saved_usd"] is not None else None
        ),
        "comparison_table": _json(answer["comparison_table"]),
        "reasoning": _json(answer["reasoning"]) or {},
        "created_at": answer["created_at"].isoformat(),
    }
