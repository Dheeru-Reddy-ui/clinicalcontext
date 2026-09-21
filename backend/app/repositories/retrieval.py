"""Data access for retrieval traces (one row per pipeline stage)."""

from __future__ import annotations

from uuid import UUID

from app.repositories.base import PgConnection
from app.retrieval.types import RetrievedChunk


class RetrievalRepository:
    async def insert_stage_trace(
        self,
        conn: PgConnection,
        *,
        query_id: UUID,
        org_id: UUID,
        stage: str,
        chunks: list[RetrievedChunk],
        duration_ms: float,
    ) -> None:
        await conn.execute(
            "INSERT INTO public.retrieval_traces "
            "(query_id, org_id, stage, chunk_ids, scores, duration_ms) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            query_id,
            org_id,
            stage,
            [c.chunk_id for c in chunks],
            [float(c.score) for c in chunks],
            duration_ms,
        )
