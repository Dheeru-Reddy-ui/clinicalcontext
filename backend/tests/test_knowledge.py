"""Knowledge that grows: search terms, live PubMed, Europe PMC, drug labels, the feed.

Parsing and query-building are pinned offline with recorded response
shapes. Filing documents into the corpus runs against the real database.
One test goes to the real PubMed, openFDA and Europe PMC — opt-in with
LIVE_NETWORK_TESTS=1, so CI never depends on a third party being up.
"""

from __future__ import annotations

import os
from datetime import date
from uuid import uuid4

import httpx
import pytest

from app.ingestion.models import RawDocument, RawSection
from app.knowledge.drugs import find_labels, label_document, mentions_a_medicine
from app.knowledge.feed import feed_query, items_from_summaries
from app.knowledge.live import LiveLiterature, file_documents, parse_europe_pmc, pubmed_query
from app.knowledge.specialties import SPECIALTIES, get_specialty
from app.knowledge.terms import asks_for_recent, search_term

# -- search terms ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "term"),
    [
        ("what should i take for sugar doctor?", "diabetes mellitus treatment"),
        ("my child has loose motions and fever since 2 days", "diarrhea fever child"),
        ("Latest research on semaglutide for heart failure", "semaglutide heart failure"),
        ("piles treatment", "hemorrhoids treatment"),
        ("hi", ""),
    ],
)
def test_questions_become_literature_terms(question: str, term: str) -> None:
    assert search_term(question) == term


def test_a_long_message_is_capped() -> None:
    long = " ".join(f"word{i}" for i in range(40))
    assert len(search_term(long).split()) == 8


def test_recent_is_recognised() -> None:
    assert asks_for_recent("what's new in 2026 for migraine")
    assert not asks_for_recent("what is migraine")


def test_the_pubmed_query_prefers_strong_evidence_and_can_be_recent() -> None:
    this_year = date.today().year
    strong = pubmed_query("typhoid", recent=False, high_evidence=True)
    assert "hasabstract" in strong and "randomized controlled trial[pt]" in strong
    assert f'"{this_year - 15}"[dp]' in strong
    recent = pubmed_query("typhoid", recent=True, high_evidence=False)
    assert f'"{this_year - 3}"[dp]' in recent and "[pt]" not in recent


# -- Europe PMC -------------------------------------------------------------------------


def test_europe_pmc_results_become_documents() -> None:
    payload = {
        "resultList": {
            "result": [
                {
                    "id": "38000001",
                    "source": "MED",
                    "pmid": "38000001",
                    "title": "Doxycycline versus azithromycin for scrub typhus.",
                    "abstractText": "<h4>Background</h4>Scrub typhus is common. <b>Results</b> ...",
                    "firstPublicationDate": "2024-05-02",
                    "pubTypeList": {"pubType": ["Randomized Controlled Trial"]},
                    "journalInfo": {"journal": {"title": "N Engl J Med"}},
                    "doi": "10.1/x",
                },
                {"id": "2", "title": "No abstract here", "abstractText": ""},
            ]
        }
    }
    (document,) = parse_europe_pmc(payload)
    assert document.title == "Doxycycline versus azithromycin for scrub typhus"
    assert "<" not in document.sections[0].content
    assert document.pmid == "38000001" and document.source_type == "pubmed"
    assert document.publication_date == date(2024, 5, 2)
    assert document.publication_types == ["Randomized Controlled Trial"]
    assert document.url == "https://pubmed.ncbi.nlm.nih.gov/38000001/"


# -- drug labels ------------------------------------------------------------------------


def _label(**overrides: object) -> dict[str, object]:
    label: dict[str, object] = {
        "set_id": "abc-123",
        "effective_time": "20260909",
        "openfda": {"generic_name": ["AMOXICILLIN"], "brand_name": ["Amoxil"]},
        "indications_and_usage": ["1 INDICATIONS AND USAGE Amoxicillin is indicated for ..."],
        "dosage_and_administration": ["2 DOSAGE AND ADMINISTRATION Adults: 500 mg every 12 hours."],
        "contraindications": ["4 CONTRAINDICATIONS History of a serious hypersensitivity ..."],
    }
    label.update(overrides)
    return label


def test_a_label_becomes_a_citable_document() -> None:
    document = label_document(_label())
    assert document is not None
    assert document.source_type == "drug_label"
    assert document.title == "Amoxicillin — FDA drug label (Amoxil)"
    assert document.publication_date == date(2026, 9, 9)
    assert document.url == "https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid=abc-123"
    headings = [s.title for s in document.sections]
    assert headings == ["Indications and usage", "Dosage and administration", "Contraindications"]
    assert document.sections[1].content.startswith("Adults: 500 mg")


def test_a_label_without_a_generic_name_is_skipped() -> None:
    assert label_document(_label(openfda={})) is None


def test_medicine_questions_are_recognised() -> None:
    assert mentions_a_medicine("What is the dose of amoxicillin for a child?")
    assert mentions_a_medicine("side effects of metformin")
    assert not mentions_a_medicine("What causes asthma?")


async def test_labels_are_looked_up_by_exact_generic_name() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["search"] = request.url.params["search"]
        return httpx.Response(200, json={"results": [_label(), _label(set_id="older")]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        documents = await find_labels("dose of amoxicillin in pneumonia", client=client)
    assert '"AMOXICILLIN"' in seen["search"] and "generic_name.exact" in seen["search"]
    assert len(documents) == 1, "one label per medicine"


async def test_no_matching_label_is_an_empty_list() -> None:
    transport = httpx.MockTransport(lambda _r: httpx.Response(404, json={"error": {}}))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await find_labels("what is asthma", client=client) == []


# -- the research feed ------------------------------------------------------------------


def test_the_feed_query_asks_for_major_topics_and_strong_designs() -> None:
    query = feed_query(get_specialty("cardiology") or pytest.fail(), days=90)
    assert "[majr]" in query and "[MeSH Terms]" not in query
    assert "meta-analysis[pt]" in query and "english[lang]" in query


def test_feed_items_carry_the_design() -> None:
    items = items_from_summaries(
        [
            {
                "uid": "1",
                "title": "A guideline.",
                "fulljournalname": "Circulation",
                "sortpubdate": "2026/09/01 00:00",
                "pubtype": ["Journal Article", "Practice Guideline"],
            },
            {"uid": "", "title": "missing id"},
        ]
    )
    assert [(i.title, i.design, i.published) for i in items] == [
        ("A guideline", "Guideline", "2026/09/01")
    ]


def test_every_specialty_has_topics_and_a_search() -> None:
    assert len(SPECIALTIES) >= 40
    for specialty in SPECIALTIES:
        assert specialty.topics and specialty.pubmed, specialty.slug


# -- filing into the corpus (real database) ----------------------------------------------

needs_db = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL"
)


@needs_db
async def test_filed_documents_are_retrievable_and_never_filed_twice(
    migrated_database: str,
) -> None:
    import asyncpg

    from app.retrieval.embed import EmbeddingService
    from app.retrieval.embedders import get_embedder
    from app.retrieval.pipeline import RetrievalConfig, RetrievalPipeline
    from app.retrieval.rerank import get_reranker

    marker = f"zzqx{uuid4().hex[:8]}"
    document = RawDocument(
        source_type="pubmed",
        external_id=f"test-{marker}",
        title=f"Doxycycline for {marker} fever: a randomised trial",
        abstract="",
        sections=[
            RawSection(
                title="Abstract",
                content=f"In {marker} fever, doxycycline reduced time to defervescence compared "
                "with placebo in a randomised controlled trial of adults.",
            )
        ],
        publication_date=date(2025, 1, 1),
        publication_types=["Randomized Controlled Trial"],
    )
    pool = await asyncpg.create_pool(migrated_database, min_size=1, max_size=2)
    try:
        assert await file_documents(pool, [document]) == 1
        assert await file_documents(pool, [document]) == 0, "deduplicated"
        pipeline = RetrievalPipeline(
            pool, EmbeddingService(get_embedder("local")), get_reranker("local"), RetrievalConfig()
        )
        result = await pipeline.retrieve(f"doxycycline {marker} fever")
        assert any(marker in (c.title or "") for c in result.chunks)
        hit = next(c for c in result.chunks if marker in (c.title or ""))
        assert hit.evidence_grade == "B" and hit.study_type == "randomized_controlled_trial"
    finally:
        await pool.execute("DELETE FROM public.documents WHERE external_id = $1", f"test-{marker}")
        await pool.close()


@needs_db
async def test_a_full_database_stops_live_filing(migrated_database: str) -> None:
    import asyncpg

    class OneResultNcbi:
        async def search_ids(self, **_kw: object) -> list[str]:
            return ["1"]

        async def fetch_xml_batches(self, **_kw: object) -> list[str]:
            return [
                "<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID><Article>"
                "<ArticleTitle>T</ArticleTitle><Abstract><AbstractText>Some text about a thing"
                "</AbstractText></Abstract></Article></MedlineCitation></PubmedArticle>"
                "</PubmedArticleSet>"
            ]

    pool = await asyncpg.create_pool(migrated_database, min_size=1, max_size=2)
    try:
        live = LiveLiterature(pool, ncbi=OneResultNcbi(), max_db_mb=0)  # type: ignore[arg-type]
        result = await live.enrich("thing treatment")
        assert result.found == 1 and result.added == 0
        assert result.skipped == "corpus at its size limit"
    finally:
        await pool.close()


# -- the real services (opt-in) -----------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("LIVE_NETWORK_TESTS") != "1", reason="set LIVE_NETWORK_TESTS=1 to reach PubMed"
)
async def test_real_pubmed_openfda_and_europe_pmc() -> None:
    from app.ingestion.sources.ncbi import NcbiClient

    client = NcbiClient(timeout=10.0, max_attempts=2)
    try:
        ids = await client.search_ids(
            db="pubmed",
            term=pubmed_query("scrub typhus doxycycline", recent=False, high_evidence=True),
            limit=3,
            sort="relevance",
        )
    finally:
        await client.close()
    assert ids
    labels = await find_labels("dose of amoxicillin")
    assert labels and labels[0].title.startswith("Amoxicillin — FDA drug label")
    async with httpx.AsyncClient(timeout=10.0) as http:
        response = await http.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={
                "query": "(scrub typhus) AND HAS_ABSTRACT:y",
                "format": "json",
                "resultType": "core",
                "pageSize": "2",
            },
        )
    assert parse_europe_pmc(response.json())


@needs_db
async def test_the_nightly_refresh_files_each_specialtys_newest_papers(
    migrated_database: str,
) -> None:
    import asyncpg

    from app.knowledge.refresh import refresh_latest
    from app.knowledge.specialties import get_specialty

    marker = f"zzrf{uuid4().hex[:8]}"
    terms: list[str] = []

    class NewestNcbi:
        async def search_ids(self, *, term: str, **_kw: object) -> list[str]:
            terms.append(term)
            return ["1"]

        async def fetch_xml_batches(self, **_kw: object) -> list[str]:
            return [
                "<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>"
                f"{marker}</PMID><Article><ArticleTitle>{marker} guideline</ArticleTitle>"
                f"<Abstract><AbstractText>New {marker} guidance for adults with heart failure."
                "</AbstractText></Abstract><PublicationTypeList><PublicationType>Practice "
                "Guideline</PublicationType></PublicationTypeList></Article></MedlineCitation>"
                "</PubmedArticle></PubmedArticleSet>"
            ]

    pool = await asyncpg.create_pool(migrated_database, min_size=1, max_size=2)
    cardiology = get_specialty("cardiology") or pytest.fail()
    try:
        summary = await refresh_latest(
            pool,
            specialties=(cardiology,),
            client=NewestNcbi(),  # type: ignore[arg-type]
            max_db_mb=100_000,
        )
        assert summary["added"] == 1 and summary["specialties"] == 1
        assert "[majr]" in terms[0]
        grade = await pool.fetchval(
            "SELECT evidence_grade::text FROM public.documents WHERE external_id = $1", marker
        )
        assert grade == "A", "a practice guideline is filed as grade A"
        full = await refresh_latest(
            pool,
            specialties=(cardiology,),
            client=NewestNcbi(),
            max_db_mb=0,  # type: ignore[arg-type]
        )
        assert full["stopped_at_limit"] == 1 and full["added"] == 0
    finally:
        await pool.execute("DELETE FROM public.documents WHERE external_id = $1", marker)
        await pool.close()
