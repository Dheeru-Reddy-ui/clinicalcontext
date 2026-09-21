"""Per-tenant analytics: usage, cost, and answer quality.

Every number is computed from the rows the system actually wrote — query
status, answer cost/latency, cache flags, feedback — never estimated. Reads
run under the caller's tenant context, so one org can only ever see its own
numbers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.repositories.base import tenant_connection
from app.schemas.analytics import (
    CostLine,
    CostPoint,
    CostReport,
    QualityReport,
    TopQuery,
    UsageOverview,
    UsagePoint,
    UsageSeries,
)
from app.services import cost as ledger

if TYPE_CHECKING:
    import asyncpg

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool


def _rate(numerator: int, denominator: int) -> float | None:
    """A rate, or None when there is nothing to divide by (never a fake 0.0)."""
    return round(numerator / denominator, 4) if denominator else None


def _float(value: Any) -> float:
    return float(value) if value is not None else 0.0


class AnalyticsService:
    def __init__(self, pool: DbPool) -> None:
        self._pool = pool

    async def overview(self, *, org_id: UUID, user_id: UUID, days: int) -> UsageOverview:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            row = await conn.fetchrow(
                """
                WITH window_queries AS (
                    SELECT q.id, q.status, q.guardrail_verdict AS verdict
                    FROM public.queries q
                    WHERE q.org_id = $1 AND q.created_at >= now() - make_interval(days => $2)
                ),
                latest AS (
                    SELECT a.*
                    FROM window_queries wq
                    JOIN LATERAL (
                        SELECT * FROM public.answers
                        WHERE query_id = wq.id ORDER BY created_at DESC LIMIT 1
                    ) a ON true
                )
                SELECT
                    (SELECT count(*) FROM window_queries)                       AS total_queries,
                    (SELECT count(*) FROM window_queries WHERE status = 'blocked') AS blocked,
                    (SELECT count(*) FROM window_queries
                       WHERE verdict->>'blocked_by' = 'phi')                       AS phi_blocks,
                    (SELECT count(*) FROM window_queries
                       WHERE verdict->>'blocked_by' = 'scope')                     AS scope_blocks,
                    (SELECT count(*) FROM window_queries
                       WHERE (verdict->>'escalation')::boolean)                    AS red_flags,
                    (SELECT count(*) FROM latest WHERE NOT abstained)           AS answered,
                    (SELECT count(*) FROM latest WHERE abstained)               AS abstained,
                    (SELECT count(*) FROM latest WHERE cached)                  AS cache_hits,
                    (SELECT count(*) FROM latest WHERE has_contradiction)       AS contradictions,
                    (SELECT count(*) FROM latest)                               AS answer_count,
                    (SELECT coalesce(sum(cost_usd), 0) FROM latest)             AS total_cost,
                    (SELECT coalesce(sum(cache_saved_usd), 0) FROM latest)      AS saved_cost,
                    (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms)
                       FROM latest WHERE latency_ms IS NOT NULL)                AS p50,
                    (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                       FROM latest WHERE latency_ms IS NOT NULL)                AS p95
                """,
                org_id,
                days,
            )
        assert row is not None
        answers = int(row["answer_count"] or 0)
        return UsageOverview(
            days=days,
            total_queries=int(row["total_queries"] or 0),
            answered=int(row["answered"] or 0),
            abstained=int(row["abstained"] or 0),
            blocked=int(row["blocked"] or 0),
            guardrail_triggers={
                "phi": int(row["phi_blocks"] or 0),
                "scope": int(row["scope_blocks"] or 0),
                "red_flag": int(row["red_flags"] or 0),
            },
            cache_hits=int(row["cache_hits"] or 0),
            abstention_rate=_rate(int(row["abstained"] or 0), answers),
            cache_hit_rate=_rate(int(row["cache_hits"] or 0), answers),
            contradiction_rate=_rate(int(row["contradictions"] or 0), answers),
            total_cost_usd=round(_float(row["total_cost"]), 6),
            cost_saved_usd=round(_float(row["saved_cost"]), 6),
            latency_p50_ms=row["p50"],
            latency_p95_ms=row["p95"],
        )

    async def usage_series(self, *, org_id: UUID, user_id: UUID, days: int) -> UsageSeries:
        """Daily usage, with zero-filled days so charts have no gaps."""
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            rows = await conn.fetch(
                """
                WITH days AS (
                    SELECT generate_series(
                        (now() - make_interval(days => $2 - 1))::date, now()::date, '1 day'
                    )::date AS day
                ),
                latest AS (
                    SELECT q.created_at::date AS day, a.cost_usd, a.cache_saved_usd, a.cached
                    FROM public.queries q
                    LEFT JOIN LATERAL (
                        SELECT cost_usd, cache_saved_usd, cached FROM public.answers
                        WHERE query_id = q.id ORDER BY created_at DESC LIMIT 1
                    ) a ON true
                    WHERE q.org_id = $1 AND q.created_at >= now() - make_interval(days => $2)
                )
                SELECT d.day,
                       count(l.day) AS queries,
                       count(*) FILTER (WHERE l.cached) AS cache_hits,
                       coalesce(sum(l.cost_usd), 0) AS cost_usd,
                       coalesce(sum(l.cache_saved_usd), 0) AS cost_saved_usd
                FROM days d
                LEFT JOIN latest l ON l.day = d.day
                GROUP BY d.day
                ORDER BY d.day
                """,
                org_id,
                days,
            )
        return UsageSeries(
            days=days,
            points=[
                UsagePoint(
                    day=r["day"],
                    queries=int(r["queries"] or 0),
                    cache_hits=int(r["cache_hits"] or 0),
                    cost_usd=round(_float(r["cost_usd"]), 6),
                    cost_saved_usd=round(_float(r["cost_saved_usd"]), 6),
                )
                for r in rows
            ],
        )

    async def quality(self, *, org_id: UUID, user_id: UUID, days: int, top: int) -> QualityReport:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            latest_cte = """
                WITH latest AS (
                    SELECT a.abstained, a.has_contradiction, a.confidence, a.evidence_grade
                    FROM public.queries q
                    JOIN LATERAL (
                        SELECT * FROM public.answers
                        WHERE query_id = q.id ORDER BY created_at DESC LIMIT 1
                    ) a ON true
                    WHERE q.org_id = $1 AND q.created_at >= now() - make_interval(days => $2)
                )
            """
            totals = await conn.fetchrow(
                f"""
                {latest_cte}
                SELECT count(*) AS n,
                       count(*) FILTER (WHERE abstained) AS abstained,
                       count(*) FILTER (WHERE has_contradiction) AS contradictions
                FROM latest
                """,
                org_id,
                days,
            )
            by_confidence = await conn.fetch(
                f"""
                {latest_cte}
                SELECT coalesce(confidence::text, 'unknown') AS k, count(*) AS n
                FROM latest GROUP BY 1
                """,
                org_id,
                days,
            )
            by_grade = await conn.fetch(
                f"""
                {latest_cte}
                SELECT coalesce(evidence_grade::text, 'ungraded') AS k, count(*) AS n
                FROM latest GROUP BY 1 ORDER BY 1
                """,
                org_id,
                days,
            )
            feedback = await conn.fetch(
                """
                SELECT f.rating::text AS rating,
                       coalesce(f.reason::text, 'unspecified') AS reason,
                       count(*) AS n
                FROM public.feedback f
                WHERE f.org_id = $1 AND f.created_at >= now() - make_interval(days => $2)
                GROUP BY 1, 2
                """,
                org_id,
                days,
            )
            top_queries = await conn.fetch(
                """
                SELECT q.raw_query AS query, count(*) AS n
                FROM public.queries q
                WHERE q.org_id = $1 AND q.created_at >= now() - make_interval(days => $2)
                GROUP BY 1 ORDER BY n DESC, 1 LIMIT $3
                """,
                org_id,
                days,
                top,
            )

        assert totals is not None
        answers = int(totals["n"] or 0)
        return QualityReport(
            days=days,
            abstention_rate=_rate(int(totals["abstained"] or 0), answers),
            contradiction_rate=_rate(int(totals["contradictions"] or 0), answers),
            by_confidence={r["k"]: int(r["n"]) for r in by_confidence},
            by_evidence_grade={r["k"]: int(r["n"]) for r in by_grade},
            feedback_up=sum(int(r["n"]) for r in feedback if r["rating"] == "up"),
            feedback_down=sum(int(r["n"]) for r in feedback if r["rating"] == "down"),
            feedback_by_reason={
                r["reason"]: int(r["n"]) for r in feedback if r["rating"] == "down"
            },
            top_queries=[TopQuery(query=r["query"], count=int(r["n"])) for r in top_queries],
        )

    async def cost(self, *, org_id: UUID, user_id: UUID, days: int) -> CostReport:
        """Spend per component from the cost ledger: what each provider
        billed, what the same units would cost on the cloud provider a local
        stand-in replaces, and what the caches avoided."""
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            lines = await conn.fetch(
                """
                SELECT component::text AS component, provider, model, unit, cached,
                       count(*) AS calls, sum(units) AS units, sum(cost_usd) AS cost_usd
                FROM public.cost_events
                WHERE org_id = $1 AND created_at >= now() - make_interval(days => $2)
                GROUP BY component, provider, model, unit, cached
                ORDER BY component, provider, model, unit, cached
                """,
                org_id,
                days,
            )
            daily = await conn.fetch(
                """
                WITH days AS (
                    SELECT generate_series(
                        (now() - make_interval(days => $2 - 1))::date, now()::date, '1 day'
                    )::date AS day
                ),
                events AS (
                    SELECT created_at::date AS day, component::text AS component, provider,
                           model, unit, cached, sum(units) AS units, sum(cost_usd) AS cost_usd
                    FROM public.cost_events
                    WHERE org_id = $1 AND created_at >= now() - make_interval(days => $2)
                    GROUP BY 1, 2, 3, 4, 5, 6
                )
                SELECT d.day, e.component, e.provider, e.model, e.unit, e.cached,
                       e.units, e.cost_usd
                FROM days d LEFT JOIN events e ON e.day = d.day
                ORDER BY d.day
                """,
                org_id,
                days,
            )
            month = await conn.fetch(
                """
                SELECT component::text AS component, provider, model, unit,
                       sum(units) AS units, sum(cost_usd) AS cost_usd
                FROM public.cost_events
                WHERE org_id = $1 AND cached AND component IN ('rerank', 'generation')
                  AND created_at >= date_trunc('month', now())
                GROUP BY component, provider, model, unit
                """,
                org_id,
            )

        def projected(row: Any) -> float:
            return _float(row["units"]) * ledger.unit_price(
                row["component"], row["provider"], row["model"], row["unit"]
            )

        keyed: dict[tuple[str, str, str, str], CostLine] = {}
        for r in lines:
            key = (r["component"], r["provider"], r["model"], r["unit"])
            line = keyed.setdefault(
                key,
                CostLine(
                    component=key[0],
                    provider=key[1],
                    model=key[2],
                    unit=key[3],
                    calls=0,
                    units=0.0,
                    cost_usd=0.0,
                    projected_usd=0.0,
                    cached_units=0.0,
                    cached_saved_usd=0.0,
                    cached_projected_usd=0.0,
                ),
            )
            if r["cached"]:
                line.cached_units += _float(r["units"])
                line.cached_saved_usd += _float(r["cost_usd"])
                line.cached_projected_usd += projected(r)
            else:
                line.calls += int(r["calls"])
                line.units += _float(r["units"])
                line.cost_usd += _float(r["cost_usd"])
                line.projected_usd += projected(r)
        out_lines = [_round_line(line) for line in keyed.values()]

        by_component: dict[str, float] = dict.fromkeys(ledger.COMPONENTS, 0.0)
        by_projected: dict[str, float] = dict.fromkeys(ledger.COMPONENTS, 0.0)
        for line in out_lines:
            by_component[line.component] += line.cost_usd
            by_projected[line.component] += line.projected_usd
        semantic = [line for line in out_lines if line.component in ("rerank", "generation")]
        embedding = [line for line in out_lines if line.component == "embedding"]

        points: dict[Any, CostPoint] = {}
        for r in daily:
            point = points.setdefault(
                r["day"],
                CostPoint(
                    day=r["day"],
                    cost_usd=0.0,
                    projected_usd=0.0,
                    saved_usd=0.0,
                    saved_projected_usd=0.0,
                ),
            )
            if r["component"] is None:
                continue
            if r["cached"]:
                point.saved_usd += _float(r["cost_usd"])
                point.saved_projected_usd += projected(r)
            else:
                point.cost_usd += _float(r["cost_usd"])
                point.projected_usd += projected(r)

        return CostReport(
            days=days,
            total_cost_usd=_r6(sum(line.cost_usd for line in out_lines)),
            total_projected_usd=_r6(sum(line.projected_usd for line in out_lines)),
            semantic_cache_saved_usd=_r6(sum(line.cached_saved_usd for line in semantic)),
            semantic_cache_saved_projected_usd=_r6(
                sum(line.cached_projected_usd for line in semantic)
            ),
            embedding_cache_saved_usd=_r6(sum(line.cached_saved_usd for line in embedding)),
            embedding_cache_saved_projected_usd=_r6(
                sum(line.cached_projected_usd for line in embedding)
            ),
            month_semantic_cache_saved_usd=_r6(sum(_float(r["cost_usd"]) for r in month)),
            month_semantic_cache_saved_projected_usd=_r6(sum(projected(r) for r in month)),
            offline=any(line.provider == ledger.LOCAL_PROVIDER for line in out_lines),
            price_checked=ledger.PRICE_CHECKED,
            by_component={k: _r6(v) for k, v in by_component.items()},
            by_component_projected={k: _r6(v) for k, v in by_projected.items()},
            lines=out_lines,
            series=[_round_point(p) for p in points.values()],
        )


def _r6(value: float) -> float:
    return round(value, 6)


def _round_line(line: CostLine) -> CostLine:
    return line.model_copy(
        update={
            "units": round(line.units, 3),
            "cost_usd": _r6(line.cost_usd),
            "projected_usd": _r6(line.projected_usd),
            "cached_units": round(line.cached_units, 3),
            "cached_saved_usd": _r6(line.cached_saved_usd),
            "cached_projected_usd": _r6(line.cached_projected_usd),
        }
    )


def _round_point(point: CostPoint) -> CostPoint:
    return point.model_copy(
        update={
            "cost_usd": _r6(point.cost_usd),
            "projected_usd": _r6(point.projected_usd),
            "saved_usd": _r6(point.saved_usd),
            "saved_projected_usd": _r6(point.saved_projected_usd),
        }
    )
