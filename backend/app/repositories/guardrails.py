"""Persistence for guardrail verdicts: queries.guardrail_verdict + audit_log."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.repositories.base import PgConnection
from app.repositories.tenancy import TenancyRepository
from app.schemas.guardrails import GuardrailStatus


class GuardrailsRepository:
    def __init__(self) -> None:
        self._audit = TenancyRepository()

    async def record_pre_retrieval(
        self,
        conn: PgConnection,
        *,
        query_id: UUID,
        org_id: UUID,
        user_id: UUID | None,
        verdict: dict[str, Any],
        status: GuardrailStatus,
        blocked_by: str | None,
    ) -> None:
        """Write the verdict onto the query and an immutable audit row (one txn)."""
        async with conn.transaction():
            await conn.execute(
                "UPDATE public.queries SET guardrail_verdict = $2::jsonb, status = $3 "
                "WHERE id = $1 AND org_id = $4",
                query_id,
                json.dumps(verdict),
                "blocked" if status == "blocked" else "running",
                org_id,
            )
            await self._audit.insert_audit(
                conn,
                org_id=org_id,
                user_id=user_id,
                action="guardrail.pre_retrieval",
                resource_type="query",
                resource_id=query_id,
                payload={"status": status, "blocked_by": blocked_by, "verdict": verdict},
            )

    async def record_grounding(
        self,
        conn: PgConnection,
        *,
        query_id: UUID,
        org_id: UUID,
        user_id: UUID | None,
        finding: dict[str, Any],
    ) -> None:
        async with conn.transaction():
            await self._audit.insert_audit(
                conn,
                org_id=org_id,
                user_id=user_id,
                action="guardrail.grounding",
                resource_type="query",
                resource_id=query_id,
                payload=finding,
            )
