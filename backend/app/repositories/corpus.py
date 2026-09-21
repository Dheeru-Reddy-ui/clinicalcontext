"""Data access for the shared public corpus (service context).

Corpus writes target org_id IS NULL rows — exactly what RLS reserves for the
service role — so these methods run on the service connection, never through
tenant_connection. Dedupe rides the per-scope content_hash unique indexes (migration 023):
global for the shared corpus, per organisation for private uploads.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

import asyncpg

from app.ingestion.models import Chunk, Classification, RawDocument
from app.repositories.base import PgConnection


class CorpusRepository:
    async def insert_document_with_chunks(
        self,
        conn: PgConnection,
        *,
        document: RawDocument,
        content_hash: str,
        classification: Classification,
        chunks: list[Chunk],
    ) -> UUID | None:
        """Insert a public-corpus document and its chunks atomically.

        Returns the new document id, or None when content_hash already exists
        (dedupe — nothing is written, including chunks).
        """
        classification_payload: dict[str, Any] = {
            "study_type": classification.study_type,
            "evidence_grade": classification.evidence_grade,
            "method": classification.method,
            "reasoning": classification.reasoning,
            "signals": classification.signals,
            "model": classification.model,
            "prompt_version": classification.prompt_version,
        }
        metadata_payload: dict[str, Any] = {
            "publication_types": document.publication_types,
            "mesh_terms": document.mesh_terms,
        }
        async with conn.transaction():
            document_id = await conn.fetchval(
                """
                INSERT INTO public.documents (
                    org_id, source_type, external_id, title, abstract, authors,
                    journal, publication_date, doi, pmid, url, study_type,
                    evidence_grade, storage_path, content_hash, metadata, classification
                ) VALUES (
                    NULL, $1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10,
                    $11, $12, $13, $14, $15::jsonb, $16::jsonb
                )
                ON CONFLICT (content_hash) WHERE org_id IS NULL DO NOTHING
                RETURNING id
                """,
                document.source_type,
                document.external_id,
                document.title,
                document.abstract,
                json.dumps(document.authors),
                document.journal,
                document.publication_date,
                document.doi,
                document.pmid,
                document.url,
                classification.study_type,
                classification.evidence_grade,
                document.storage_path,
                content_hash,
                json.dumps(metadata_payload),
                json.dumps(classification_payload),
            )
            if document_id is None:
                return None
            await conn.executemany(
                """
                INSERT INTO public.chunks
                    (document_id, chunk_index, content, token_count, section, metadata)
                VALUES ($1, $2, $3, $4, $5, '{}'::jsonb)
                """,
                [(document_id, c.chunk_index, c.content, c.token_count, c.section) for c in chunks],
            )
            return UUID(str(document_id))

    async def record_failure(
        self,
        conn: PgConnection,
        *,
        source: str,
        external_id: str | None,
        stage: str,
        error: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO public.ingestion_failures (source, external_id, stage, error, payload)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            """,
            source,
            external_id,
            stage,
            error[:2000],
            json.dumps(payload or {}),
        )

    async def fetch_chunks_pending_embedding(
        self, conn: PgConnection, *, limit: int
    ) -> list[asyncpg.Record]:
        """Chunks with no row yet in chunk_embeddings.

        Embeds ``coalesce(embed_text, content)`` so context-enriched strategies
        (structural) embed the augmented text while the stored passage stays
        clean. org_id + strategy ride along for the denormalized target row.
        """
        rows = await conn.fetch(
            """
            SELECT c.id, c.org_id, c.strategy, coalesce(c.embed_text, c.content) AS text
            FROM public.chunks c
            LEFT JOIN public.chunk_embeddings e ON e.chunk_id = c.id
            WHERE e.chunk_id IS NULL
            ORDER BY c.id
            LIMIT $1
            """,
            limit,
        )
        return list(rows)

    async def fetch_document_chunks_pending_embedding(
        self, conn: PgConnection, *, document_id: UUID
    ) -> list[asyncpg.Record]:
        """Pending chunks of ONE document — the private-upload path, which must
        embed only what the tenant just uploaded, not the whole backlog."""
        rows = await conn.fetch(
            """
            SELECT c.id, c.org_id, c.strategy, coalesce(c.embed_text, c.content) AS text
            FROM public.chunks c
            LEFT JOIN public.chunk_embeddings e ON e.chunk_id = c.id
            WHERE e.chunk_id IS NULL AND c.document_id = $1
            ORDER BY c.chunk_index
            """,
            document_id,
        )
        return list(rows)

    async def insert_private_document_with_chunks(
        self,
        conn: PgConnection,
        *,
        org_id: UUID,
        document: RawDocument,
        content_hash: str,
        classification: Classification,
        chunks: list[Chunk],
    ) -> UUID | None:
        """Insert a tenant-private document (org_id = the caller's org).

        Must run inside ``tenant_connection``: the documents INSERT policy
        requires ``org_id = app.user_org_id()``, so RLS — not this code — is
        what guarantees a tenant cannot file a document under another org.
        Returns None when content_hash already exists (global dedupe).
        """
        classification_payload: dict[str, Any] = {
            "study_type": classification.study_type,
            "evidence_grade": classification.evidence_grade,
            "method": classification.method,
            "reasoning": classification.reasoning,
            "signals": classification.signals,
            "model": classification.model,
            "prompt_version": classification.prompt_version,
        }
        metadata_payload: dict[str, Any] = {
            "publication_types": document.publication_types,
            "mesh_terms": document.mesh_terms,
        }
        async with conn.transaction():
            document_id = await conn.fetchval(
                """
                INSERT INTO public.documents (
                    org_id, source_type, external_id, title, abstract, authors,
                    journal, publication_date, doi, pmid, url, study_type,
                    evidence_grade, storage_path, content_hash, metadata, classification
                ) VALUES (
                    $1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11,
                    $12, $13, $14, $15, $16::jsonb, $17::jsonb
                )
                ON CONFLICT (org_id, content_hash) WHERE org_id IS NOT NULL DO NOTHING
                RETURNING id
                """,
                org_id,
                document.source_type,
                document.external_id,
                document.title,
                document.abstract,
                json.dumps(document.authors),
                document.journal,
                document.publication_date,
                document.doi,
                document.pmid,
                document.url,
                classification.study_type,
                classification.evidence_grade,
                document.storage_path,
                content_hash,
                json.dumps(metadata_payload),
                json.dumps(classification_payload),
            )
            if document_id is None:
                return None
            await conn.executemany(
                """
                INSERT INTO public.chunks
                    (document_id, chunk_index, content, token_count, section, metadata)
                VALUES ($1, $2, $3, $4, $5, '{}'::jsonb)
                """,
                [(document_id, c.chunk_index, c.content, c.token_count, c.section) for c in chunks],
            )
            return UUID(str(document_id))

    async def count_chunks_pending_embedding(self, conn: PgConnection) -> int:
        value = await conn.fetchval(
            "SELECT count(*) FROM public.chunks c "
            "LEFT JOIN public.chunk_embeddings e ON e.chunk_id = c.id "
            "WHERE e.chunk_id IS NULL"
        )
        return int(value) if value is not None else 0

    async def write_embeddings(
        self, conn: PgConnection, rows: list[tuple[UUID, UUID | None, str, list[float]]]
    ) -> None:
        """Upsert vectors into chunk_embeddings. Vectors travel as pgvector
        text literals — no client-side codec needed."""
        await conn.executemany(
            """
            INSERT INTO public.chunk_embeddings (chunk_id, org_id, strategy, embedding)
            VALUES ($1, $2, $3, $4::vector)
            ON CONFLICT (chunk_id) DO UPDATE SET embedding = excluded.embedding
            """,
            [
                (
                    chunk_id,
                    org_id,
                    strategy,
                    "[" + ",".join(f"{value:.8f}" for value in vector) + "]",
                )
                for chunk_id, org_id, strategy, vector in rows
            ],
        )

    async def rebuild_lexeme_stats(
        self, conn: PgConnection, *, strategy: str = "structural"
    ) -> int:
        """Recompute lexeme document frequencies from the corpus (ts_stat).

        Drives lexical query pruning. Slow (scans every content_tsv), so it is
        a CLI/maintenance operation, not part of a migration or an ingest.
        Returns the number of distinct lexemes stored.
        """
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", strategy):
            raise ValueError(f"invalid strategy name: {strategy!r}")
        # ts_stat's argument is itself a SQL query (text), so the strategy
        # predicate is embedded as a literal; it is validated above, never
        # user-supplied.
        inner = f"SELECT content_tsv FROM public.chunks WHERE strategy = '{strategy}'"
        async with conn.transaction():
            await conn.execute("TRUNCATE public.lexeme_stats")
            await conn.execute(
                "INSERT INTO public.lexeme_stats (lexeme, df) "
                "SELECT word, ndoc FROM ts_stat($1) WHERE ndoc > 1 "
                "ON CONFLICT (lexeme) DO UPDATE SET df = excluded.df",
                inner,
            )
        value = await conn.fetchval("SELECT count(*) FROM public.lexeme_stats")
        return int(value) if value is not None else 0

    async def fetch_documents_pending_classification(
        self, conn: PgConnection, *, limit: int
    ) -> list[asyncpg.Record]:
        """Documents left ungraded (LLM was unavailable at ingest time)."""
        rows = await conn.fetch(
            "SELECT id, title, abstract, metadata FROM public.documents "
            "WHERE study_type IS NULL ORDER BY ingested_at LIMIT $1",
            limit,
        )
        return list(rows)

    async def update_classification(
        self,
        conn: PgConnection,
        *,
        document_id: UUID,
        study_type: str | None,
        evidence_grade: str | None,
        classification: dict[str, Any],
    ) -> None:
        await conn.execute(
            "UPDATE public.documents "
            "SET study_type = $2, evidence_grade = $3, classification = $4::jsonb "
            "WHERE id = $1",
            document_id,
            study_type,
            evidence_grade,
            json.dumps(classification),
        )

    async def count_documents_pending_classification(self, conn: PgConnection) -> int:
        value = await conn.fetchval(
            "SELECT count(*) FROM public.documents WHERE study_type IS NULL"
        )
        return int(value) if value is not None else 0

    async def corpus_stats(self, conn: PgConnection) -> dict[str, Any]:
        documents = await conn.fetchval("SELECT count(*) FROM public.documents")
        chunks = await conn.fetchval("SELECT count(*) FROM public.chunks")
        pending = await self.count_chunks_pending_embedding(conn)
        by_source = await conn.fetch(
            "SELECT source_type::text AS k, count(*) AS n FROM public.documents GROUP BY 1"
        )
        by_grade = await conn.fetch(
            "SELECT coalesce(evidence_grade::text, 'ungraded') AS k, count(*) AS n "
            "FROM public.documents GROUP BY 1 ORDER BY 1"
        )
        by_study = await conn.fetch(
            "SELECT coalesce(study_type::text, 'unclassified') AS k, count(*) AS n "
            "FROM public.documents GROUP BY 1 ORDER BY n DESC"
        )
        return {
            "documents": int(documents or 0),
            "chunks": int(chunks or 0),
            "chunks_pending_embedding": pending,
            "by_source": {r["k"]: int(r["n"]) for r in by_source},
            "by_evidence_grade": {r["k"]: int(r["n"]) for r in by_grade},
            "by_study_type": {r["k"]: int(r["n"]) for r in by_study},
        }
