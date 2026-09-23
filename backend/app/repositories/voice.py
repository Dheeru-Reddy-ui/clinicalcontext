"""Voice-turn persistence and the tenant's voice aggregates (migration 019)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.guardrails.phi import withhold_phi
from app.repositories.base import PgConnection


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


class VoiceRepository:
    async def insert_turn(
        self,
        conn: PgConnection,
        *,
        org_id: UUID,
        user_id: UUID | None,
        voice_session_id: UUID,
        query_session_id: UUID | None,
        query_id: UUID | None,
        turn_index: int,
        backend: str,
        stt_model: str,
        tts_model: str,
        transcript_raw: str,
        transcript_final: str,
        corrections: list[dict[str, Any]],
        confirmation: dict[str, Any],
        outcome: str,
        blocked_by: str | None,
        latency: dict[str, Any],
        speculation: dict[str, Any],
        barge_in: dict[str, Any],
        waste: dict[str, Any],
        mask_used: bool,
    ) -> UUID:
        row = await conn.fetchval(
            """
            INSERT INTO public.voice_turns (
                org_id, user_id, voice_session_id, query_session_id, query_id, turn_index,
                backend, stt_model, tts_model, transcript_raw, transcript_final,
                corrections, confirmation, outcome, blocked_by, latency, speculation,
                barge_in, waste, mask_used
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                $12::jsonb, $13::jsonb, $14::public.voice_turn_outcome, $15,
                $16::jsonb, $17::jsonb, $18::jsonb, $19::jsonb, $20
            )
            RETURNING id
            """,
            org_id,
            user_id,
            voice_session_id,
            query_session_id,
            query_id,
            turn_index,
            backend,
            stt_model,
            tts_model,
            # A spoken question the PHI gate blocked keeps no transcript.
            withhold_phi(transcript_raw),
            withhold_phi(transcript_final),
            json.dumps(corrections),
            json.dumps(confirmation),
            outcome,
            blocked_by,
            json.dumps(latency),
            json.dumps(speculation),
            json.dumps(barge_in),
            json.dumps(waste),
            mask_used,
        )
        return UUID(str(row))

    async def merge_latency(
        self, conn: PgConnection, *, org_id: UUID, turn_id: UUID, latency: dict[str, Any]
    ) -> None:
        """Fold client-reported timings into a turn written before they arrived."""
        await conn.execute(
            "UPDATE public.voice_turns SET latency = latency || $3::jsonb "
            "WHERE id = $1 AND org_id = $2",
            turn_id,
            org_id,
            json.dumps(latency),
        )

    async def merge_waste(
        self, conn: PgConnection, *, org_id: UUID, turn_id: UUID, waste: dict[str, Any]
    ) -> None:
        await conn.execute(
            "UPDATE public.voice_turns SET waste = waste || $3::jsonb "
            "WHERE id = $1 AND org_id = $2",
            turn_id,
            org_id,
            json.dumps(waste),
        )

    async def list_turns(
        self,
        conn: PgConnection,
        *,
        org_id: UUID,
        query_session_id: UUID | None,
        voice_session_id: UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses = ["org_id = $1"]
        params: list[Any] = [org_id]
        if query_session_id is not None:
            params.append(query_session_id)
            clauses.append(f"query_session_id = ${len(params)}")
        if voice_session_id is not None:
            params.append(voice_session_id)
            clauses.append(f"voice_session_id = ${len(params)}")
        where = " AND ".join(clauses)
        total = await conn.fetchval(
            f"SELECT count(*) FROM public.voice_turns WHERE {where}", *params
        )
        params.extend([limit, offset])
        rows = await conn.fetch(
            f"SELECT * FROM public.voice_turns WHERE {where} "
            f"ORDER BY created_at DESC LIMIT ${len(params) - 1} OFFSET ${len(params)}",
            *params,
        )
        return [self._row(r) for r in rows], int(total or 0)

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        out = dict(row)
        for key in ("corrections", "confirmation", "latency", "speculation", "barge_in", "waste"):
            out[key] = _json(out.get(key))
        return out

    async def aggregate(
        self, conn: PgConnection, *, org_id: UUID, days: int
    ) -> list[dict[str, Any]]:
        rows = await conn.fetch(
            """
            SELECT outcome, backend, latency, speculation, confirmation, barge_in, waste,
                   mask_used, corrections
            FROM public.voice_turns
            WHERE org_id = $1 AND created_at >= now() - ($2::int * interval '1 day')
            """,
            org_id,
            days,
        )
        return [self._row(r) for r in rows]

    async def get_tts_quality(self, conn: PgConnection, *, org_id: UUID) -> str:
        value = await conn.fetchval(
            "SELECT voice_tts_quality FROM public.organizations WHERE id = $1", org_id
        )
        return str(value or "flash")

    async def set_tts_quality(self, conn: PgConnection, *, org_id: UUID, quality: str) -> None:
        await conn.execute(
            "UPDATE public.organizations SET voice_tts_quality = $2 WHERE id = $1", org_id, quality
        )
