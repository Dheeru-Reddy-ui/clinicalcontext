"""Query autocomplete: MeSH vocabulary + this org's own query history.

Two sources, deliberately: MeSH terms teach the user the vocabulary the corpus
is actually indexed under, while their own history is what they are most
likely reaching for. Both are trigram-indexed (migration 014), and both
queries are bounded by LIMIT, which is what keeps p95 in the tens of ms.

History is read under the caller's tenant context, so one org's autocomplete
can never surface another org's questions.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import UUID

from app.repositories.base import tenant_connection
from app.schemas.suggest import Suggestion, SuggestOut

if TYPE_CHECKING:
    import asyncpg

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

# Below this the index cannot help and every term matches — not worth a round trip.
MIN_PREFIX = 2

# MeSH "check tags": indexers attach these to almost every record, so they
# describe the study population, not the topic. Suggesting "humans" to someone
# typing "hu" is pure noise, so they never enter the autocomplete vocabulary.
CHECK_TAGS = frozenset(
    {
        "humans",
        "animals",
        "male",
        "female",
        "adult",
        "aged",
        "aged, 80 and over",
        "middle aged",
        "young adult",
        "adolescent",
        "child",
        "child, preschool",
        "infant",
        "infant, newborn",
        "mice",
        "rats",
    }
)

# A term on more than this share of the corpus narrows nothing either. The
# ratio guard catches generic terms the static list above does not name.
_MAX_CORPUS_SHARE = 0.25


async def rebuild_mesh_terms(pool: DbPool) -> int:
    """Repopulate mesh_terms from the corpus. Service context (shared data).

    Idempotent: a full rebuild, so terms whose documents were removed stop
    being suggested. Returns the number of distinct terms stored.
    """
    async with pool.acquire() as conn, conn.transaction():
        corpus_size = await conn.fetchval("SELECT count(*) FROM public.documents")
        ceiling = max(int(int(corpus_size or 0) * _MAX_CORPUS_SHARE), 1)
        await conn.execute("TRUNCATE public.mesh_terms")
        await conn.execute(
            """
            INSERT INTO public.mesh_terms (term, document_count)
            SELECT lower(trim(t.term)), count(DISTINCT d.id) AS n
            FROM public.documents d
            CROSS JOIN LATERAL jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(d.metadata->'mesh_terms') = 'array'
                     THEN d.metadata->'mesh_terms' ELSE '[]'::jsonb END
            ) AS t(term)
            WHERE length(trim(t.term)) BETWEEN 3 AND 120
              AND lower(trim(t.term)) <> ALL($1::text[])
            GROUP BY 1
            HAVING count(DISTINCT d.id) <= $2
            ON CONFLICT (term) DO UPDATE SET document_count = excluded.document_count
            """,
            sorted(CHECK_TAGS),
            ceiling,
        )
        total = await conn.fetchval("SELECT count(*) FROM public.mesh_terms")
    return int(total or 0)


class SuggestService:
    def __init__(self, pool: DbPool) -> None:
        self._pool = pool

    async def suggest(self, *, org_id: UUID, user_id: UUID, query: str, limit: int) -> SuggestOut:
        started = time.perf_counter()
        prefix = query.strip()
        if len(prefix) < MIN_PREFIX:
            return SuggestOut(query=query, suggestions=[], took_ms=0.0)

        lowered = prefix.lower()
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            # The caller's own recent questions first — highest intent.
            history = await conn.fetch(
                """
                SELECT q.raw_query AS value, count(*) AS weight
                FROM public.queries q
                WHERE q.org_id = $1 AND q.raw_query ILIKE '%' || $2 || '%'
                GROUP BY 1
                ORDER BY max(q.created_at) DESC
                LIMIT $3
                """,
                org_id,
                lowered,
                limit,
            )
            remaining = max(limit - len(history), 0)
            mesh = (
                await conn.fetch(
                    """
                    SELECT term AS value, document_count AS weight
                    FROM public.mesh_terms
                    WHERE term ILIKE $1 || '%' OR term % $1
                    ORDER BY (term ILIKE $1 || '%') DESC,
                             similarity(term, $1) DESC,
                             document_count DESC
                    LIMIT $2
                    """,
                    lowered,
                    remaining,
                )
                if remaining
                else []
            )

        suggestions = [
            Suggestion(value=r["value"], kind="history", weight=int(r["weight"])) for r in history
        ] + [Suggestion(value=r["value"], kind="mesh", weight=int(r["weight"])) for r in mesh]
        return SuggestOut(
            query=query,
            suggestions=suggestions,
            took_ms=round((time.perf_counter() - started) * 1000, 2),
        )
