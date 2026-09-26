"""Retrieval stages against a real Postgres, isolated by a unique strategy label.

Seeding chunks under a per-test ``strategy`` keeps these deterministic and
free of interference from the main corpus, while still exercising the real
pgvector, full-text, fusion, rerank, boost, and trace-writing paths.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.retrieval.dense import dense_search
from app.retrieval.embed import EmbeddingService
from app.retrieval.embedders import get_embedder
from app.retrieval.lexical import lexical_search
from app.retrieval.pipeline import RetrievalConfig, RetrievalPipeline
from app.retrieval.rerank import BM25Reranker

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (needs a running Postgres; see README)",
)

_DOCS = [
    (
        "Apixaban versus warfarin in atrial fibrillation with chronic kidney disease",
        "Apixaban reduced stroke and major bleeding compared with warfarin in patients "
        "with atrial fibrillation and stage 4 chronic kidney disease.",
    ),
    (
        "Community acquired pneumonia empiric antibiotic therapy",
        "Empiric therapy for community acquired pneumonia in immunocompetent adults "
        "typically includes a beta-lactam plus a macrolide.",
    ),
    (
        "Metformin as first line therapy in type 2 diabetes",
        "Metformin remains first line pharmacologic therapy for glycemic control in "
        "type 2 diabetes mellitus given its efficacy and safety.",
    ),
]


@dataclass
class RetrievalEnv:
    pool: asyncpg.Pool[asyncpg.Record]
    admin: asyncpg.Connection
    strategy: str
    doc_ids: list[UUID]


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vector) + "]"


@pytest.fixture
async def env(migrated_database: str) -> AsyncIterator[RetrievalEnv]:
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)
    admin = await asyncpg.connect(TEST_DATABASE_URL)
    salt = uuid4().hex[:8]
    strategy = f"test_{salt}"
    embedder = get_embedder("local")

    doc_ids: list[UUID] = []
    for i, (title, body) in enumerate(_DOCS):
        [vector] = await embedder.embed_documents([body])
        doc_id = await admin.fetchval(
            "INSERT INTO public.documents "
            "(org_id, source_type, title, content_hash, publication_date, evidence_grade) "
            "VALUES (NULL, 'pubmed', $1, $2, $3, $4) RETURNING id",
            f"{title} [{salt}]",
            f"hash-{salt}-{i}",
            date(2020 + i, 1, 1),
            ["A", "B", "C"][i],
        )
        doc_ids.append(doc_id)
        chunk_id = await admin.fetchval(
            "INSERT INTO public.chunks "
            "(document_id, org_id, chunk_index, content, token_count, strategy) "
            "VALUES ($1, NULL, 0, $2, $3, $4) RETURNING id",
            doc_id,
            body,
            len(body) // 4,
            strategy,
        )
        await admin.execute(
            "INSERT INTO public.chunk_embeddings (chunk_id, org_id, strategy, embedding) "
            "VALUES ($1, NULL, $2, $3::vector)",
            chunk_id,
            strategy,
            _vector_literal(vector),
        )

    try:
        yield RetrievalEnv(pool=pool, admin=admin, strategy=strategy, doc_ids=doc_ids)
    finally:
        await admin.execute("DELETE FROM public.documents WHERE id = ANY($1)", doc_ids)
        await admin.close()
        await pool.close()


async def test_dense_search_ranks_on_topic_first(env: RetrievalEnv) -> None:
    embedder = get_embedder("local")
    [query_vector] = await embedder.embed_queries(
        ["apixaban anticoagulation atrial fibrillation kidney disease"]
    )
    async with env.pool.acquire() as conn:
        results = await dense_search(
            conn, query_vector=query_vector, org_id=None, strategy=env.strategy, limit=3
        )
    assert results
    assert "Apixaban" in results[0].title  # type: ignore[operator]
    assert results[0].score > 0
    assert results[0].components["dense"] == results[0].score


async def test_lexical_search_expands_abbreviations(env: RetrievalEnv) -> None:
    # Query uses "afib" — the synonym table must expand it to "atrial fibrillation"
    # so the apixaban document (which never contains the token "afib") is found.
    # A control query with only "afib" (no expansion match in body) finds nothing.
    async with env.pool.acquire() as conn:
        expanded = await lexical_search(
            conn, query_text="afib", org_id=None, strategy=env.strategy, limit=3
        )
        no_synonyms = await lexical_search(
            conn,
            query_text="afib",
            org_id=None,
            strategy=env.strategy,
            limit=3,
            synonyms={},  # disable expansion → the "afib" token is absent from the corpus
        )
    assert any("Apixaban" in (r.title or "") for r in expanded)
    assert no_synonyms == []  # without expansion, bare "afib" matches nothing


async def test_full_pipeline_runs_all_stages(env: RetrievalEnv) -> None:
    service = EmbeddingService(get_embedder("local"))
    config = RetrievalConfig(strategy=env.strategy, rerank_top_n=3)
    pipeline = RetrievalPipeline(env.pool, service, BM25Reranker(), config)

    result = await pipeline.retrieve(
        "metformin first line therapy for type 2 diabetes", keep_stage_snapshots=True
    )

    assert result.chunks
    stages = {t.stage for t in result.timings}
    assert {"embed_query", "dense", "lexical", "fused", "reranked", "boosted"} <= stages
    assert "Metformin" in (result.chunks[0].title or "")
    # Boost transparency survives to the final chunk.
    assert "post_boost" in result.chunks[0].components


async def test_stage_switches_turn_stages_off_not_down(env: RetrievalEnv) -> None:
    """The ablation baseline is dense-only: no lexical candidates, no fusion
    scores, no reranker, no boosts — the dense cosine ranking as it stands."""
    service = EmbeddingService(get_embedder("local"))
    config = RetrievalConfig(
        strategy=env.strategy, rerank_top_n=3, lexical=False, rerank=False, boosts=False
    )
    pipeline = RetrievalPipeline(env.pool, service, BM25Reranker(), config)
    result = await pipeline.retrieve("metformin type 2 diabetes", keep_stage_snapshots=True)
    assert result.chunks and result.stages["lexical"] == []
    assert result.chunks == result.stages["dense"][:3]
    for chunk in result.chunks:
        assert "rrf" not in chunk.components and "post_boost" not in chunk.components


async def test_pipeline_writes_a_trace_row_per_stage(env: RetrievalEnv) -> None:
    # A trace needs real query + org rows (FKs). Build the minimal chain.
    org_id = await env.admin.fetchval(
        "INSERT INTO public.organizations (name, slug) VALUES ('Trace Org', $1) RETURNING id",
        f"trace-{uuid4().hex[:8]}",
    )
    user_id = await env.admin.fetchval(
        "INSERT INTO auth.users (email) VALUES ($1) RETURNING id",
        f"trace-{uuid4().hex[:8]}@cc-tests.org",
    )
    session_id = await env.admin.fetchval(
        "INSERT INTO public.query_sessions (org_id, user_id) VALUES ($1, $2) RETURNING id",
        org_id,
        user_id,
    )
    query_id = await env.admin.fetchval(
        "INSERT INTO public.queries (session_id, org_id, user_id, raw_query, status) "
        "VALUES ($1, $2, $3, 'apixaban in ckd', 'running') RETURNING id",
        session_id,
        org_id,
        user_id,
    )
    try:
        service = EmbeddingService(get_embedder("local"))
        config = RetrievalConfig(strategy=env.strategy, rerank_top_n=3)
        pipeline = RetrievalPipeline(env.pool, service, BM25Reranker(), config)

        await pipeline.retrieve("apixaban anticoagulation", org_id=org_id, query_id=query_id)

        traces = await env.admin.fetch(
            "SELECT stage, chunk_ids, scores, duration_ms FROM public.retrieval_traces "
            "WHERE query_id = $1 ORDER BY stage",
            query_id,
        )
        stages = {r["stage"] for r in traces}
        assert stages == {"dense", "lexical", "fused", "reranked", "boosted"}
        for row in traces:
            assert len(row["chunk_ids"]) == len(row["scores"])
            assert row["duration_ms"] is not None
    finally:
        await env.admin.execute("SET session_replication_role = 'replica'")
        await env.admin.execute("DELETE FROM public.audit_log WHERE org_id = $1", org_id)
        await env.admin.execute("RESET session_replication_role")
        await env.admin.execute("DELETE FROM public.organizations WHERE id = $1", org_id)
        await env.admin.execute("DELETE FROM auth.users WHERE id = $1", user_id)


async def test_dense_search_uses_the_hnsw_index_and_is_deterministic(env: RetrievalEnv) -> None:
    """Two things that are easy to lose and hard to notice.

    The ANN ordering has to be the only thing in the inner ORDER BY — an HNSW
    index scan cannot satisfy a two-column ordering, so a tie-breaker placed
    there turns the query into a sequential scan over every vector (442 ms
    against 4 ms on the seeded corpus). And the result still has to be stable
    across runs, because a retrieval metric is only reproducible if retrieval
    is.
    """
    embedder = get_embedder("local")
    vector = (await embedder.embed_queries(["metformin in type 2 diabetes"]))[0]

    async with env.pool.acquire() as conn:
        # The app sets this per connection; the test pool does not, so it is
        # set here to match production.
        await conn.execute("SET hnsw.ef_search = 100")
        plan = "\n".join(
            row[0]
            for row in await conn.fetch(
                "EXPLAIN SELECT ann.chunk_id FROM ("
                "  SELECT e.chunk_id, e.embedding <=> $1::vector AS distance"
                "  FROM public.chunk_embeddings e"
                "  WHERE e.strategy = 'structural' AND e.org_id IS NULL"
                "  ORDER BY e.embedding <=> $1::vector LIMIT 50"
                ") ann ORDER BY ann.distance, ann.chunk_id",
                _vector_literal(vector),
            )
        )
        assert "chunk_embeddings_hnsw" in plan, f"the HNSW index must be used:\n{plan}"
        assert "Seq Scan on chunk_embeddings" not in plan, plan

        # ef_search has to be at least the candidate count, or the tenant
        # filter (applied after the index) leaves the search short.
        ef_search = int(await conn.fetchval("SHOW hnsw.ef_search"))
        assert ef_search >= 50, f"ef_search={ef_search} is below the candidate count"

        first = await dense_search(conn, query_vector=vector, org_id=None, limit=25)
        second = await dense_search(conn, query_vector=vector, org_id=None, limit=25)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert len(first) == 25, "the search returns as many candidates as it was asked for"


async def _seed_passage(env: RetrievalEnv, title: str, body: str, *, embed: bool) -> UUID:
    """One document with one chunk under the test's strategy; its chunk id."""
    doc_id = await env.admin.fetchval(
        "INSERT INTO public.documents (org_id, source_type, title, content_hash) "
        "VALUES (NULL, 'pubmed', $1, $2) RETURNING id",
        title,
        f"hash-{uuid4().hex}",
    )
    env.doc_ids.append(doc_id)
    chunk_id: UUID = await env.admin.fetchval(
        "INSERT INTO public.chunks (document_id, org_id, chunk_index, content, token_count, "
        "strategy) VALUES ($1, NULL, 0, $2, $3, $4) RETURNING id",
        doc_id,
        body,
        len(body) // 4,
        env.strategy,
    )
    if embed:
        [vector] = await get_embedder("local").embed_documents([body])
        await env.admin.execute(
            "INSERT INTO public.chunk_embeddings (chunk_id, org_id, strategy, embedding) "
            "VALUES ($1, NULL, $2, $3::vector)",
            chunk_id,
            env.strategy,
            _vector_literal(vector),
        )
    return chunk_id


async def test_the_only_passage_with_a_rare_term_reaches_the_reranker(env: RetrievalEnv) -> None:
    """The flaky-test failure, made deterministic. ts_rank_cd does not weigh a
    term by rarity, so passages repeating a question's common words outrank
    the only one carrying its rarest; and the vector index can miss a vector
    (here it has none — as for a paper filed moments ago). Found by one list
    below the cut, the passage used to be dropped at fusion, before the
    reranker could see it."""
    token = f"zzqx{uuid4().hex[:8]}"
    for i in range(12):
        await _seed_passage(
            env,
            f"Doxycycline and fever {i}",
            "Doxycycline shortened fever; fever resolved on doxycycline, and doxycycline "
            "was continued until the fever settled.",
            embed=True,
        )
    rare = await _seed_passage(
        env,
        f"Doxycycline in {token} fever",
        f"In {token} fever, doxycycline reduced time to defervescence.",
        embed=False,
    )
    query = f"doxycycline {token} fever"

    async with env.pool.acquire() as conn:
        ranked = await lexical_search(
            conn,
            query_text=query,
            org_id=None,
            strategy=env.strategy,
            limit=5,
            synonyms={},
            rare_terms=False,
        )
        with_rare = await lexical_search(
            conn, query_text=query, org_id=None, strategy=env.strategy, limit=5, synonyms={}
        )
    assert rare not in {c.chunk_id for c in ranked}, "ranked below the common-word passages"
    assert [c.chunk_id for c in with_rare if "rare_term" in c.components] == [rare]
    assert len(with_rare) == 6, "the top five, then the rare-term passage"

    config = RetrievalConfig(
        strategy=env.strategy, dense_limit=5, lexical_limit=5, fused_limit=5, rerank_top_n=3
    )
    service = EmbeddingService(get_embedder("local"))
    found = await RetrievalPipeline(env.pool, service, BM25Reranker(), config).retrieve(query)
    assert found.chunks[0].chunk_id == rare, "kept through fusion, first after reranking"
    assert found.chunks[0].components["rare_term"] == 1.0

    without = RetrievalConfig(
        strategy=env.strategy,
        dense_limit=5,
        lexical_limit=5,
        fused_limit=5,
        rerank_top_n=3,
        rare_terms=False,
    )
    lost = await RetrievalPipeline(env.pool, service, BM25Reranker(), without).retrieve(query)
    assert rare not in {c.chunk_id for c in lost.chunks}, "what happened before"
