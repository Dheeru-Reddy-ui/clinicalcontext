"""The guardrail pipeline — compose the safety layers.

Pre-retrieval, in order:
  1. PHI    — block before anything else, so injected PHI never reaches an LLM.
  2. scope  — refuse diagnosis / individualized dosing / personal-care / injection.
  3. red-flag — non-blocking; attaches an escalation banner shown before output.

Post-generation:
  - grounding — prune unsupported sentences, or abstain if too much is removed.

Every pre-retrieval verdict is written to ``queries.guardrail_verdict`` and an
immutable ``audit_log`` row (when a query context is supplied). Findings are
PHI-safe (types/counts only), so persistence never re-introduces PHI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from app.guardrails.grounding import GroundingVerifier, verify_grounding
from app.guardrails.phi import PhiDetector, phi_block_message
from app.guardrails.redflag import detect_red_flags
from app.guardrails.scope import ScopeResult, classify_scope
from app.repositories.guardrails import GuardrailsRepository
from app.schemas.guardrails import (
    GroundingVerdict,
    GuardrailFinding,
    GuardrailStatus,
    PreRetrievalVerdict,
)

if TYPE_CHECKING:
    import asyncpg

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("app.guardrails.pipeline")


class GuardrailPipeline:
    def __init__(
        self,
        *,
        pool: DbPool | None = None,
        phi_detector: PhiDetector | None = None,
        allow_llm: bool = True,
    ) -> None:
        self._pool = pool
        self._phi = phi_detector if phi_detector is not None else PhiDetector()
        self._allow_llm = allow_llm
        self._repo = GuardrailsRepository()

    async def check_pre_retrieval(
        self,
        query: str,
        *,
        org_id: UUID | None = None,
        user_id: UUID | None = None,
        query_id: UUID | None = None,
    ) -> PreRetrievalVerdict:
        """Run PHI → scope → red-flag. Persists the verdict if a query context
        (pool + query_id + org_id) is available."""
        findings: list[GuardrailFinding] = []

        # 1. PHI — hard block, before any model sees the text.
        phi = self._phi.scan(query)
        if phi.detected:
            finding = GuardrailFinding(
                stage="phi",
                action="block",
                code="phi_detected",
                message=phi_block_message(),
                detail={"entities": phi.entity_counts, "via_base64": phi.decoded_layer},
            )
            verdict = PreRetrievalVerdict(
                allowed=False,
                blocked_by="phi",
                message=finding.message,
                findings=[finding],
            )
            await self._persist(verdict, org_id, user_id, query_id, "blocked", "phi")
            return verdict

        # 2. Scope — refuse out-of-scope framings.
        scope: ScopeResult = await classify_scope(query, allow_llm=self._allow_llm)
        if scope.verdict != "allow":
            finding = GuardrailFinding(
                stage="scope",
                action="block",
                code=scope.reason_code,
                message=scope.message,
                detail={"verdict": scope.verdict, "method": scope.method},
            )
            verdict = PreRetrievalVerdict(
                allowed=False,
                blocked_by="scope",
                message=scope.message,
                findings=[finding],
            )
            await self._persist(verdict, org_id, user_id, query_id, "blocked", "scope")
            return verdict

        # 3. Red-flag — non-blocking escalation banner.
        red_flag = detect_red_flags(query)
        if red_flag.triggered:
            findings.append(
                GuardrailFinding(
                    stage="red_flag",
                    action="escalate",
                    code="red_flag_emergency",
                    message="Emergency indicators detected; escalation banner attached.",
                    detail={"categories": red_flag.categories},
                )
            )

        verdict = PreRetrievalVerdict(
            allowed=True,
            escalation_banner=red_flag.banner,
            findings=findings,
        )
        status: GuardrailStatus = "escalated" if red_flag.triggered else "clean"
        await self._persist(verdict, org_id, user_id, query_id, status, None)
        return verdict

    async def verify_answer(
        self,
        answer: str,
        citations: dict[int, str],
        *,
        verifier: GroundingVerifier | None = None,
        org_id: UUID | None = None,
        user_id: UUID | None = None,
        query_id: UUID | None = None,
    ) -> GroundingVerdict:
        """Post-generation grounding check, with optional audit persistence."""
        result = await verify_grounding(answer, citations, verifier=verifier)
        if self._pool is not None and query_id is not None and org_id is not None:
            try:
                async with self._pool.acquire() as conn:
                    await self._repo.record_grounding(
                        conn,
                        query_id=query_id,
                        org_id=org_id,
                        user_id=user_id,
                        finding=result.finding.model_dump(),
                    )
            except Exception as exc:  # audit is never fatal to the answer path
                logger.warning("grounding_audit_failed", error=f"{type(exc).__name__}: {exc}")
        return result

    async def _persist(
        self,
        verdict: PreRetrievalVerdict,
        org_id: UUID | None,
        user_id: UUID | None,
        query_id: UUID | None,
        status: GuardrailStatus,
        blocked_by: str | None,
    ) -> None:
        if self._pool is None or query_id is None or org_id is None:
            return
        try:
            async with self._pool.acquire() as conn:
                await self._repo.record_pre_retrieval(
                    conn,
                    query_id=query_id,
                    org_id=org_id,
                    user_id=user_id,
                    verdict=verdict.as_verdict_json(),
                    status=status,
                    blocked_by=blocked_by,
                )
        except Exception as exc:  # audit is observability; never fail the guard
            logger.warning("guardrail_audit_failed", error=f"{type(exc).__name__}: {exc}")
