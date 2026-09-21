"""Pipeline integration: idempotency, persistence, dead-lettering (real DB)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import asyncpg
import pytest

from app.ingestion.models import RawDocument, RawSection
from app.ingestion.pipeline import run_ingest
from app.repositories.corpus import CorpusRepository

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (needs a running Postgres; see README)",
)


@pytest.fixture
async def pool(migrated_database: str) -> AsyncIterator[asyncpg.Pool[asyncpg.Record]]:
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
def salt() -> str:
    return uuid4().hex[:10]


@pytest.fixture
async def _cleanup(pool: asyncpg.Pool[asyncpg.Record], salt: str) -> AsyncIterator[None]:
    yield
    await pool.execute("DELETE FROM public.documents WHERE title LIKE $1", f"%{salt}%")
    await pool.execute("DELETE FROM public.ingestion_failures WHERE source = $1", f"test-{salt}")


def _make_documents(salt: str, count: int) -> list[RawDocument]:
    documents = []
    for i in range(count):
        documents.append(
            RawDocument(
                source_type="pubmed",
                external_id=f"{salt}-{i}",
                title=f"Study {i} on topic {salt}",
                abstract=f"Background for study {i}. We measured outcome {salt} carefully.",
                sections=[
                    RawSection(
                        title="Abstract",
                        content=(
                            f"Background for study {i}. We measured outcome {salt} "
                            "carefully across two arms and report the difference."
                        ),
                    )
                ],
                pmid=f"9{i}00{salt[:4]}",
                publication_types=["Randomized Controlled Trial"],
            )
        )
    return documents


async def _stream(documents: list[RawDocument]) -> AsyncIterator[RawDocument]:
    for document in documents:
        yield document


async def test_pipeline_persists_and_is_idempotent(
    pool: asyncpg.Pool[asyncpg.Record], salt: str, _cleanup: None
) -> None:
    documents = _make_documents(salt, 3)

    first = await run_ingest(
        pool, _stream(documents), source=f"test-{salt}", allow_llm=False, embed=False
    )
    assert first.fetched == 3
    assert first.inserted == 3
    assert first.deduplicated == 0
    assert first.chunks_written >= 3

    # The gate condition: identical re-run adds zero rows.
    second = await run_ingest(
        pool, _stream(documents), source=f"test-{salt}", allow_llm=False, embed=False
    )
    assert second.inserted == 0
    assert second.deduplicated == 3

    count = await pool.fetchval(
        "SELECT count(*) FROM public.documents WHERE title LIKE $1", f"%{salt}%"
    )
    assert count == 3


async def test_pipeline_stores_classification_with_reasoning(
    pool: asyncpg.Pool[asyncpg.Record], salt: str, _cleanup: None
) -> None:
    await run_ingest(
        pool,
        _stream(_make_documents(salt, 1)),
        source=f"test-{salt}",
        allow_llm=False,
        embed=False,
    )
    row = await pool.fetchrow(
        "SELECT study_type::text AS study_type, evidence_grade::text AS grade, "
        "classification, org_id FROM public.documents WHERE title LIKE $1",
        f"%{salt}%",
    )
    assert row is not None
    assert row["org_id"] is None  # shared public corpus
    assert row["study_type"] == "randomized_controlled_trial"
    assert row["grade"] == "B"

    import json

    classification = json.loads(row["classification"])
    assert classification["method"] == "publication_types"
    assert classification["reasoning"]
    assert classification["signals"]


async def test_pipeline_skips_documents_without_text(
    pool: asyncpg.Pool[asyncpg.Record], salt: str, _cleanup: None
) -> None:
    empty = RawDocument(
        source_type="pubmed",
        external_id=f"{salt}-empty",
        title=f"Empty record {salt}",
        sections=[],
    )
    stats = await run_ingest(
        pool, _stream([empty]), source=f"test-{salt}", allow_llm=False, embed=False
    )
    assert stats.skipped_no_text == 1
    assert stats.inserted == 0


async def test_failed_document_dead_letters_without_killing_the_run(
    pool: asyncpg.Pool[asyncpg.Record],
    salt: str,
    _cleanup: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = _make_documents(salt, 2)
    poison_title = documents[0].title

    original = CorpusRepository.insert_document_with_chunks

    async def poisoned(self: CorpusRepository, conn: object, **kwargs: object) -> object:
        document = kwargs["document"]
        assert isinstance(document, RawDocument)
        if document.title == poison_title:
            raise RuntimeError("simulated persistence failure")
        return await original(self, conn, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(CorpusRepository, "insert_document_with_chunks", poisoned)

    stats = await run_ingest(
        pool, _stream(documents), source=f"test-{salt}", allow_llm=False, embed=False
    )

    assert stats.failed == 1
    assert stats.inserted == 1  # the healthy document still landed

    failure = await pool.fetchrow(
        "SELECT stage, error, payload FROM public.ingestion_failures WHERE source = $1",
        f"test-{salt}",
    )
    assert failure is not None
    assert failure["stage"] == "persist"
    assert "simulated persistence failure" in failure["error"]
