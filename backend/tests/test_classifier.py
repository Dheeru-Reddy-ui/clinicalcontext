"""Rule-based study classification: priorities, grades, and stored reasoning."""

from __future__ import annotations

import pytest

from app.ingestion.classifier import classify_document, classify_from_metadata
from app.ingestion.models import RawDocument, RawSection


def _doc(
    publication_types: list[str] | None = None,
    mesh_terms: list[str] | None = None,
) -> RawDocument:
    return RawDocument(
        source_type="pubmed",
        title="Some clinical paper",
        sections=[RawSection(title="Abstract", content="Findings were found.")],
        abstract="Findings were found.",
        publication_types=publication_types or [],
        mesh_terms=mesh_terms or [],
    )


@pytest.mark.parametrize(
    ("publication_types", "expected_type", "expected_grade"),
    [
        (["Meta-Analysis"], "meta_analysis", "A"),
        (["Systematic Review"], "systematic_review", "A"),
        (["Randomized Controlled Trial"], "randomized_controlled_trial", "B"),
        (["Practice Guideline"], "clinical_guideline", "A"),
        (["Observational Study"], "cohort_study", "B"),
        (["Case Reports"], "case_report", "D"),
        (["Review"], "narrative_review", "D"),
        (["Editorial"], "narrative_review", "D"),
    ],
)
def test_publication_types_map_to_grades(
    publication_types: list[str], expected_type: str, expected_grade: str
) -> None:
    result = classify_from_metadata(_doc(publication_types))
    assert result is not None
    assert result.study_type == expected_type
    assert result.evidence_grade == expected_grade
    assert result.method == "publication_types"
    assert result.reasoning  # the reasoning is stored, never just the grade
    assert result.signals


def test_strongest_design_wins_when_multiple_labels() -> None:
    result = classify_from_metadata(
        _doc(["Randomized Controlled Trial", "Meta-Analysis", "Journal Article"])
    )
    assert result is not None
    assert result.study_type == "meta_analysis"
    assert result.evidence_grade == "A"


def test_rct_reasoning_states_the_size_limitation() -> None:
    result = classify_from_metadata(_doc(["Randomized Controlled Trial"]))
    assert result is not None
    assert "Sample size" in result.reasoning
    assert "grade B" in result.reasoning or "defaults to grade B" in result.reasoning


def test_mesh_terms_classify_when_publication_types_are_generic() -> None:
    result = classify_from_metadata(_doc(["Journal Article"], mesh_terms=["Cohort Studies"]))
    assert result is not None
    assert result.study_type == "cohort_study"
    assert result.evidence_grade == "B"
    assert "cohort studies" in result.signals


def test_nonrandomized_trial_grades_c() -> None:
    result = classify_from_metadata(_doc(["Clinical Trial"]))
    assert result is not None
    assert result.study_type == "other"
    assert result.evidence_grade == "C"
    assert "non-randomized" in result.reasoning


def test_no_signals_returns_none() -> None:
    assert classify_from_metadata(_doc([])) is None


async def test_classify_document_without_llm_is_honestly_unclassified() -> None:
    result = await classify_document(_doc([]), allow_llm=False)
    assert result.method == "unclassified"
    assert result.study_type is None
    assert result.evidence_grade is None
    assert "ungraded" in result.reasoning


async def test_classify_document_prefers_rules_over_llm() -> None:
    # allow_llm=True but rules match → no LLM call is ever attempted.
    result = await classify_document(_doc(["Meta-Analysis"]), allow_llm=True)
    assert result.method == "publication_types"
    assert result.study_type == "meta_analysis"
