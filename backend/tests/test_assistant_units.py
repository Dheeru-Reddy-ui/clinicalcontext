"""The chat assistant's pure parts: picking passages, quoting, ranking, routing.

Each was written after a real question went wrong on a real corpus:
unrelated passages cited because one on-topic passage satisfied a coverage
test, abstracts quoted by their methods sentence, a marker after a full
stop attributed to the next sentence, "loose motions" missing "diarrhoea".
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from app.assistant.chat import ChatAssistant, rank_evidence, transient_chunks
from app.assistant.extractive import (
    best_sentence,
    extractive_answer,
    pick_passages,
    us_spelling,
    word_relevance,
)
from app.guardrails.grounding import as_sentences
from app.ingestion.models import RawDocument, RawSection
from app.retrieval.types import RetrievedChunk
from app.treatment.suggest import suggest_complaint


def _chunk(content: str, **kw: object) -> RetrievedChunk:
    fields: dict[str, object] = {
        "chunk_id": uuid4(),
        "document_id": uuid4(),
        "content": content,
        "section": None,
        "title": None,
        "publication_date": None,
        "evidence_grade": None,
        "study_type": None,
    }
    fields.update(kw)
    return RetrievedChunk(**fields)  # type: ignore[arg-type]


def test_only_passages_about_the_question_are_kept() -> None:
    on = _chunk("Doxycycline and azithromycin are effective treatments for scrub typhus.")
    off = _chunk("Sepsis remains a leading cause of mortality in intensive care units.")
    assert ChatAssistant.relevant("What is the treatment for scrub typhus?", [on, off]) == [on]


def test_lay_words_find_passages_written_in_medical_words() -> None:
    passage = _chunk(
        "Oral rehydration solution is the mainstay of treating acute diarrhoea in children."
    )
    assert ChatAssistant.relevant("my child has loose motions", [passage]) == [passage]
    assert word_relevance("diarrhea child", passage) == 1.0
    assert (
        us_spelling("Paediatric haemorrhage and diarrhoea") == "pediatric hemorrhage and diarrhea"
    )


def test_the_quoted_sentence_is_a_finding_not_a_method() -> None:
    passage = _chunk(
        "We conducted a network meta-analysis using the frequentist method. "
        "Doxycycline and azithromycin are effective treatment options for scrub typhus."
    )
    assert best_sentence("treatment for scrub typhus", passage) == (
        "Doxycycline and azithromycin are effective treatment options for scrub typhus."
    )


def test_quotes_carry_their_marker_before_the_full_stop_and_one_per_paper() -> None:
    shared = uuid4()
    chunks = [
        _chunk("Drug A is effective for scrub typhus in adults.", document_id=shared),
        _chunk("Drug A reduced fever duration in scrub typhus.", document_id=shared),
        _chunk("Drug B is also effective for scrub typhus."),
    ]
    text, used = extractive_answer("scrub typhus treatment", chunks)
    assert used == [1, 3], "one sentence per paper"
    assert text.splitlines()[0].endswith("[1].")


def test_conclusions_are_read_before_background() -> None:
    passages = [
        _chunk("background", section="Background"),
        _chunk("methods", section="Methods"),
        _chunk("results", section="Results"),
        _chunk("conclusions", section="Conclusions"),
    ]
    assert [p.content for p in pick_passages(passages)] == ["conclusions", "results"]


def test_markdown_is_split_into_statements_for_the_check() -> None:
    markdown = "## Summary\n- Drug A works [1].\n- Drug B works [2]\n**Note**\n1. Third [3]."
    assert as_sentences(markdown) == "Drug A works [1].\nDrug B works [2].\nThird [3]."


def test_ranking_prefers_relevant_strong_recent_evidence_and_drops_duplicates() -> None:
    weak = _chunk("Scrub typhus case report.", evidence_grade="D", pmid="1")
    strong = _chunk(
        "Scrub typhus treatment: doxycycline is effective.",
        evidence_grade="A",
        pmid="2",
        publication_date=date(2024, 1, 1),
    )
    duplicate = _chunk("Scrub typhus case report.", evidence_grade="D", pmid="1")
    ranked = rank_evidence("scrub typhus treatment", [weak, strong, duplicate])
    assert ranked[0] is strong and len(ranked) == 2


def test_a_document_outside_the_corpus_still_yields_citable_passages() -> None:
    document = RawDocument(
        source_type="pubmed",
        external_id="99",
        pmid="99",
        title="Antibiotics for scrub typhus",
        sections=[
            RawSection("Background", "Scrub typhus is common in Asia and causes fever."),
            RawSection("Conclusions", "Doxycycline is effective for scrub typhus in adults."),
        ],
        publication_types=["Systematic Review"],
        url="https://pubmed.ncbi.nlm.nih.gov/99/",
    )
    first = transient_chunks(document)
    again = transient_chunks(document)
    assert first[0].content.startswith("Doxycycline"), "conclusions first"
    assert first[0].evidence_grade == "A" and first[0].study_type == "systematic_review"
    assert [c.chunk_id for c in first] == [c.chunk_id for c in again], "stable identity"


def test_personal_complaints_are_offered_the_symptom_check() -> None:
    assert suggest_complaint("my child has loose motions since 2 days what to do") == (
        "diarrhoea_vomiting"
    )
    assert suggest_complaint("I have fever and body pain") == "fever"
    assert suggest_complaint("burning urine what medicine") == "urinary"
    assert suggest_complaint("my back pain is bad") == "body_pain"
    # Literature questions are not personal guidance.
    assert suggest_complaint("What is the pathophysiology of diarrhoea?") is None
    assert suggest_complaint("what is the treatment of migraine") is None


def test_generic_and_population_words_do_not_make_a_passage_relevant() -> None:
    # Seen on the live site: "first-line treatment for scrub typhus in adults"
    # was answered from psychiatry guidelines that mention "first", "treatment"
    # and "adults" — so the library looked sufficient and PubMed was skipped.
    psychiatry = _chunk(
        "Clinical practice guideline on the choice of first antipsychotic treatment "
        "for adults with schizophrenia."
    )
    on_topic = _chunk("Doxycycline is an effective first-line treatment for scrub typhus.")
    question = "What is the first-line treatment for scrub typhus in adults?"
    assert ChatAssistant.relevant(question, [psychiatry, on_topic]) == [on_topic]


def test_a_longer_topic_needs_most_of_its_words() -> None:
    from app.assistant.extractive import core_words, required_hits

    assert core_words("first-line treatment scrub typhus adults") == ["scrub", "typhus"]
    assert [required_hits(n) for n in (1, 2, 3, 4, 5)] == [1, 2, 2, 3, 3]


def test_the_clinician_voice_never_states_an_unsourced_dose() -> None:
    # Seen on the live site: with no source on leptospirosis, the model filled
    # in doses and a renal adjustment as "standard practice". The clinician
    # prompt in use forbids numbers that are not in the sources.
    from app.assistant.chat import PROMPTS
    from app.prompts.loader import load_prompt

    name, version = PROMPTS["clinician"]
    text = load_prompt(name, version).text
    assert "Never state a dose, duration" in text
    assert "with no numbers" in text
