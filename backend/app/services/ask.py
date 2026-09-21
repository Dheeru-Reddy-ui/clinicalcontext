"""The query flow: idempotency → cache → guardrails → graph → cost → persist.

Ties together Phase 7 (guardrails), Phase 8 (the agent graph), and the Phase 9
API features (semantic cache, idempotency, real cost accounting, comparison
mode, multi-turn contextualization). Yields SSE-shaped dicts: reasoning-progress
events, then answer tokens, then a terminal ``result`` (or ``blocked`` /
``cached``) event. Everything runs under the caller's tenant context.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from app.config import get_settings
from app.core.idempotency import IdempotencyStore
from app.core.telemetry import bind_context
from app.graph.graph import AgentGraph
from app.graph.reasoner import Reasoner, get_reasoner
from app.graph.state import GraphEvent
from app.guardrails.pipeline import GuardrailPipeline
from app.repositories.answers import AnswersRepository, reasoning_payload
from app.repositories.base import tenant_connection
from app.repositories.tenancy import TenancyRepository
from app.retrieval.embed import EmbeddingService
from app.retrieval.embedders import get_embedder
from app.retrieval.pipeline import RetrievalConfig, RetrievalPipeline
from app.retrieval.rerank import get_reranker
from app.retrieval.types import RetrievedChunk
from app.schemas.answer import AnswerResult
from app.services import cost
from app.services.comparison import ComparisonBuilder
from app.services.semantic_cache import SemanticCache
from app.services.webhooks import enqueue_event

if TYPE_CHECKING:
    import asyncpg
    from redis.asyncio import Redis

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("app.services.ask")

_QUERY_TYPE_ENUM = frozenset(
    ("therapy", "diagnosis", "prognosis", "etiology", "guideline_comparison", "other")
)


def _backend_components() -> tuple[str, str, str]:
    """(reasoner, embedder, reranker) names for the configured AI backend."""
    if get_settings().ai_backend == "cloud":
        return "llm", "cohere", "cohere"
    return "heuristic", "local", "local"


def _tokenize_answer(text: str) -> list[str]:
    """Split an answer into stream-able tokens (word + trailing space)."""
    return [w + " " for w in text.split()] if text else []


class AskService:
    def __init__(self, pool: DbPool, redis: Redis | None = None) -> None:
        self._pool = pool
        self._redis = redis
        self._answers = AnswersRepository()

    async def ask(
        self,
        *,
        query: str,
        org_id: UUID,
        user_id: UUID,
        session_id: UUID | None = None,
        mode: str = "standard",
        entities: list[str] | None = None,
        pico: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        auth_kind: str = "jwt",
        api_key_prefix: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        # Every provider call made for this query lands in one cost collector
        # (embedding, rerank, generation — and what the caches avoided), and
        # is written to the ledger once the stream is done, whatever path the
        # query took.
        with cost.collecting() as collector:
            try:
                async for event in self._ask(
                    collector,
                    query=query,
                    org_id=org_id,
                    user_id=user_id,
                    session_id=session_id,
                    mode=mode,
                    entities=entities,
                    pico=pico,
                    idempotency_key=idempotency_key,
                    auth_kind=auth_kind,
                    api_key_prefix=api_key_prefix,
                ):
                    yield event
            finally:
                await cost.flush(self._pool, collector, org_id=org_id)

    async def _ask(
        self,
        collector: cost.Collector,
        *,
        query: str,
        org_id: UUID,
        user_id: UUID,
        session_id: UUID | None,
        mode: str,
        entities: list[str] | None,
        pico: dict[str, Any] | None,
        idempotency_key: str | None,
        auth_kind: str,
        api_key_prefix: str | None,
    ) -> AsyncIterator[dict[str, Any]]:
        # 0. Idempotency replay — return the stored events, no second run.
        idem = IdempotencyStore(self._redis) if (self._redis and idempotency_key) else None
        if idem and idempotency_key:
            replayed = await idem.get(org_id, idempotency_key)
            if replayed is not None:
                for event in replayed:
                    yield {**event, "replayed": True}
                return

        collected: list[dict[str, Any]] = []

        async def emit(event: dict[str, Any]) -> dict[str, Any]:
            collected.append(event)
            return event

        # 1. Session + query provisioning (tenant context).
        reasoner_name, embedder_name, reranker_name = _backend_components()
        embedder = EmbeddingService(get_embedder(embedder_name), self._redis)
        query_id, resolved_session, contextualized = await self.provision(
            query=query,
            org_id=org_id,
            user_id=user_id,
            session_id=session_id,
            mode=mode,
            pico=pico,
        )
        bind_context(query_id=str(query_id), channel="text")
        collector.tag(org_id=org_id, query_id=query_id)
        yield await emit(
            {
                "stage": "accepted",
                "message": "Query received.",
                "data": {
                    "query_id": str(query_id),
                    "session_id": str(resolved_session),
                    "contextualized_query": contextualized,
                },
            }
        )

        # 1b. API-key traffic is audited with its prefix.
        if auth_kind == "api_key":
            async with self._pool.acquire() as conn:
                await TenancyRepository().insert_audit(
                    conn,
                    org_id=org_id,
                    user_id=user_id,
                    action="api.request",
                    resource_type="query",
                    resource_id=query_id,
                    payload={"api_key_prefix": api_key_prefix, "mode": mode},
                )

        # 2. Guardrails (pre-retrieval) on the contextualized query.
        guardrails = GuardrailPipeline(
            pool=self._pool, allow_llm=get_settings().ai_backend == "cloud"
        )
        verdict = await guardrails.check_pre_retrieval(
            contextualized, org_id=org_id, user_id=user_id, query_id=query_id
        )
        if not verdict.allowed:
            code = verdict.findings[0].code if verdict.findings else None
            event = await emit(
                {
                    "stage": "blocked",
                    "message": verdict.message or "Blocked by a safety guardrail.",
                    "data": {"blocked_by": verdict.blocked_by, "code": code},
                }
            )
            yield event
            if idem and idempotency_key:
                await idem.put(org_id, idempotency_key, collected)
            return
        if verdict.escalation_banner:
            yield await emit(
                {"stage": "escalation", "message": verdict.escalation_banner, "data": {}}
            )

        started = time.perf_counter()

        # 3. Semantic cache (standard mode only).
        query_vector = await embedder.embed_query(contextualized)
        if mode == "standard" and self._redis is not None:
            hit = await SemanticCache(self._redis).lookup(org_id, query_vector)
            if hit is not None:
                # What the stored answer took to produce is what this hit avoided.
                cost.replay_cached(hit.ledger)
                async for event in self._serve_cached(
                    conn_org=org_id,
                    user_id=user_id,
                    query_id=query_id,
                    payload=hit.payload,
                    saved_usd=hit.saved_usd,
                    similarity=hit.similarity,
                    query_type=hit.payload.get("query_type", "other"),
                ):
                    yield await emit(event)
                if idem and idempotency_key:
                    await idem.put(org_id, idempotency_key, collected)
                return

        # 4. Run the pipeline (comparison or standard graph).
        reasoner = get_reasoner(reasoner_name)
        retrieval = RetrievalPipeline(
            self._pool, embedder, get_reranker(reranker_name), RetrievalConfig()
        )

        async def retrieve_fn(sub_query: str) -> list[RetrievedChunk]:
            result = await retrieval.retrieve(sub_query, org_id=org_id, query_id=query_id)
            return result.chunks

        if mode == "comparison":
            async for event in self._run_comparison(
                query=contextualized,
                entities=entities or [],
                pico=pico,
                retrieve_fn=retrieve_fn,
                reasoner=reasoner,
                org_id=org_id,
                user_id=user_id,
                query_id=query_id,
                started=started,
            ):
                yield await emit(event)
        else:
            async for event in self._run_standard(
                query=contextualized,
                retrieve_fn=retrieve_fn,
                reasoner=reasoner,
                pico=pico,
                org_id=org_id,
                user_id=user_id,
                query_id=query_id,
                query_vector=query_vector,
                started=started,
            ):
                yield await emit(event)

        if idem and idempotency_key:
            await idem.put(org_id, idempotency_key, collected)

    # -- provisioning (shared with the voice path) --------------------------------------

    async def provision(
        self,
        *,
        query: str,
        org_id: UUID,
        user_id: UUID,
        session_id: UUID | None,
        mode: str = "standard",
        pico: dict[str, Any] | None = None,
    ) -> tuple[UUID, UUID, str]:
        """Create (or reuse) the session, contextualize the query, and write
        the query row. → (query_id, session_id, contextualized_query)."""
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            resolved_session = session_id or await self._answers.create_session(
                conn, org_id=org_id, user_id=user_id, title=query[:120]
            )
            contextualized = await self._contextualize(conn, resolved_session, query)
            query_id = await self._answers.create_query(
                conn,
                session_id=resolved_session,
                org_id=org_id,
                user_id=user_id,
                raw_query=query,
                mode=mode,
                pico=pico,
                contextualized_query=contextualized if contextualized != query else None,
            )
        return query_id, resolved_session, contextualized

    async def contextualize(
        self, *, org_id: UUID, user_id: UUID, session_id: UUID | None, query: str
    ) -> str:
        """The contextualized form without writing anything (speculation on
        partial transcripts needs the same rewrite the committed turn gets)."""
        if session_id is None:
            return query
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            return await self._contextualize(conn, session_id, query)

    async def persist_result(
        self,
        *,
        org_id: UUID,
        user_id: UUID,
        query_id: UUID,
        result: AnswerResult,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> UUID:
        """Persist an answer produced outside the SSE generator (voice path)."""
        return await self._persist(
            org_id=org_id,
            user_id=user_id,
            query_id=query_id,
            result=result,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )

    async def mark_blocked(self, *, org_id: UUID, user_id: UUID, query_id: UUID) -> None:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            await self._answers.set_query_status(
                conn, query_id=query_id, org_id=org_id, status="blocked", query_type=None
            )

    # -- standard single-answer flow ---------------------------------------------------

    async def _run_standard(
        self,
        *,
        query: str,
        retrieve_fn: Any,
        reasoner: Reasoner,
        pico: dict[str, Any] | None,
        org_id: UUID,
        user_id: UUID,
        query_id: UUID,
        query_vector: list[float],
        started: float,
    ) -> AsyncIterator[dict[str, Any]]:
        graph = AgentGraph(retrieve_fn, reasoner, pico=pico)
        tags = {"tenant_id": str(org_id), "query_id": str(query_id)}
        result: AnswerResult | None = None
        async for kind, payload in graph.astream(query, tags=tags):
            if kind == "event":
                assert isinstance(payload, GraphEvent)
                yield payload.to_sse_dict()
            else:
                result = payload
        assert result is not None

        for token in _tokenize_answer(result.answer):
            yield {"stage": "token", "message": "", "data": {"text": token}}

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = reasoner.usage
        answer_id = await self._persist(
            org_id=org_id,
            user_id=user_id,
            query_id=query_id,
            result=result,
            latency_ms=latency_ms,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=usage.cost_usd,
        )
        # Cache the answer for future semantically-similar queries.
        if self._redis is not None and not result.abstained:
            collector = cost.current()
            await SemanticCache(self._redis).store(
                org_id,
                query_vector,
                _result_payload(result, answer_id),
                usage.cost_usd,
                ledger=collector.snapshot() if collector is not None else None,
            )
        yield _result_event(
            answer_id,
            query_id,
            result,
            latency_ms,
            cached=False,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=usage.cost_usd,
        )

    # -- comparison flow ---------------------------------------------------------------

    async def _run_comparison(
        self,
        *,
        query: str,
        entities: list[str],
        pico: dict[str, Any] | None,
        retrieve_fn: Any,
        reasoner: Reasoner,
        org_id: UUID,
        user_id: UUID,
        query_id: UUID,
        started: float,
    ) -> AsyncIterator[dict[str, Any]]:
        builder = ComparisonBuilder(retrieve_fn, reasoner)
        async for event in builder.build(query, entities, pico):
            if event["stage"] == "comparison_result":
                table = event["data"]["comparison_table"]
                result = builder.to_answer_result(query, entities, table, reasoner.name)
                latency_ms = int((time.perf_counter() - started) * 1000)
                answer_id = await self._persist(
                    org_id=org_id,
                    user_id=user_id,
                    query_id=query_id,
                    result=result,
                    latency_ms=latency_ms,
                    input_tokens=reasoner.usage.input_tokens,
                    output_tokens=reasoner.usage.output_tokens,
                    cost_usd=reasoner.usage.cost_usd,
                    comparison_table=table,
                )
                out = _result_event(
                    answer_id,
                    query_id,
                    result,
                    latency_ms,
                    cached=False,
                    input_tokens=reasoner.usage.input_tokens,
                    output_tokens=reasoner.usage.output_tokens,
                    cost_usd=reasoner.usage.cost_usd,
                )
                out["data"]["comparison_table"] = table
                yield out
            else:
                yield event

    # -- cached serving ----------------------------------------------------------------

    async def _serve_cached(
        self,
        *,
        conn_org: UUID,
        user_id: UUID,
        query_id: UUID,
        payload: dict[str, Any],
        saved_usd: float,
        similarity: float,
        query_type: str,
    ) -> AsyncIterator[dict[str, Any]]:
        answer_text = payload.get("answer", "")
        for token in _tokenize_answer(answer_text):
            yield {"stage": "token", "message": "", "data": {"text": token}}
        # Persist a cached answers row (cost 0, records the saving).
        async with tenant_connection(self._pool, conn_org, user_id) as conn:
            answer_id = await self._answers.save_cached_answer(
                conn, query_id=query_id, org_id=conn_org, payload=payload, saved_usd=saved_usd
            )
            await self._answers.set_query_status(
                conn,
                query_id=query_id,
                org_id=conn_org,
                status="completed",
                query_type=query_type if query_type in _QUERY_TYPE_ENUM else "other",
            )
            # A cache hit is still a completed query: whether the answer came
            # from cache is our implementation detail, not something a
            # subscriber should have to infer from missing events.
            await enqueue_event(
                conn,
                org_id=conn_org,
                event="query.completed",
                payload={
                    "query_id": str(query_id),
                    "answer_id": str(answer_id),
                    "abstained": bool(payload.get("abstained", False)),
                    "confidence": payload.get("confidence"),
                    "evidence_grade": payload.get("evidence_grade"),
                    "cost_usd": 0.0,
                    "cached": True,
                },
            )
        yield {
            "stage": "result",
            "message": "Complete (served from cache).",
            "data": {
                **payload,
                "answer_id": str(answer_id),
                "query_id": str(query_id),
                "cached": True,
                "cache_similarity": similarity,
                "cache_saved_usd": saved_usd,
                "cost_usd": 0.0,
            },
        }

    # -- helpers -----------------------------------------------------------------------

    async def _contextualize(self, conn: Any, session_id: UUID, query: str) -> str:
        """Resolve an anaphoric follow-up against the session's last query.

        "what about in pregnancy?" after "first-line treatment for hypertension?"
        becomes "first-line treatment for hypertension in pregnancy". Heuristic:
        only rewrites short/anaphoric follow-ups, and only if a prior turn exists.
        """
        anaphoric = query.strip().lower()
        looks_followup = len(anaphoric.split()) <= 8 and (
            anaphoric.startswith(("what about", "and ", "in ", "for ", "how about", "what if"))
            or " it " in f" {anaphoric} "
        )
        if not looks_followup:
            return query
        prior = await conn.fetchval(
            "SELECT coalesce(contextualized_query, raw_query) FROM public.queries "
            "WHERE session_id = $1 ORDER BY created_at DESC LIMIT 1",
            session_id,
        )
        if not prior:
            return query
        return f"{prior.rstrip('?. ')} — follow-up: {query}"

    async def _persist(
        self,
        *,
        org_id: UUID,
        user_id: UUID,
        query_id: UUID,
        result: AnswerResult,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        comparison_table: dict[str, Any] | None = None,
    ) -> UUID:
        query_type = result.query_type if result.query_type in _QUERY_TYPE_ENUM else "other"
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            answer_id = await self._answers.save_answer(
                conn,
                query_id=query_id,
                org_id=org_id,
                result=result,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                comparison_table=comparison_table,
            )
            await self._answers.set_query_status(
                conn, query_id=query_id, org_id=org_id, status="completed", query_type=query_type
            )
            # Queued in the same transaction as the answer: subscribers are
            # never told about a result that failed to commit.
            await enqueue_event(
                conn,
                org_id=org_id,
                event="query.completed",
                payload={
                    "query_id": str(query_id),
                    "answer_id": str(answer_id),
                    "abstained": result.abstained,
                    "confidence": result.confidence,
                    "evidence_grade": result.evidence_grade,
                    "cost_usd": cost_usd,
                },
            )
        return answer_id


def _result_payload(result: AnswerResult, answer_id: UUID) -> dict[str, Any]:
    return {
        "answer_id": str(answer_id),
        "answer": result.answer,
        "abstained": result.abstained,
        "confidence": result.confidence,
        "evidence_grade": result.evidence_grade,
        "contradiction": result.contradiction.model_dump(),
        "citations": [c.model_dump(mode="json") for c in result.citations],
        "query_type": result.query_type,
        "is_multi_hop": result.is_multi_hop,
        "sub_questions": result.sub_questions,
        "generation_mode": result.generation_mode,
        "model": result.model,
        # Persisted alongside the row and served on reload (see migration 018).
        "reasoning": reasoning_payload(result),
    }


def _result_event(
    answer_id: UUID,
    query_id: UUID,
    result: AnswerResult,
    latency_ms: int,
    *,
    cached: bool,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> dict[str, Any]:
    return {
        "stage": "result",
        "message": "Complete.",
        "data": {
            **_result_payload(result, answer_id),
            "query_id": str(query_id),
            "cached": cached,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
        },
    }
