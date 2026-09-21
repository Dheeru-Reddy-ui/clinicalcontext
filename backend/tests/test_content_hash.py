"""Content-hash identity: the property that makes ingestion idempotent."""

from __future__ import annotations

from app.ingestion.models import RawDocument, RawSection
from app.ingestion.pipeline import content_hash_for


def _doc(title: str, body: str) -> RawDocument:
    return RawDocument(
        source_type="pubmed",
        title=title,
        sections=[RawSection(title="Abstract", content=body)],
        abstract=body,
    )


def test_identical_documents_hash_identically() -> None:
    a = _doc("A Trial of Apixaban", "We randomized patients and measured stroke.")
    b = _doc("A Trial of Apixaban", "We randomized patients and measured stroke.")
    assert content_hash_for(a) == content_hash_for(b)


def test_hash_is_whitespace_and_case_insensitive() -> None:
    a = _doc("A Trial of Apixaban", "We randomized patients.")
    b = _doc("  a   TRIAL  of   apixaban ", "we   RANDOMIZED   patients.")
    assert content_hash_for(a) == content_hash_for(b)


def test_different_title_changes_hash() -> None:
    a = _doc("Apixaban trial", "Identical body text here.")
    b = _doc("Rivaroxaban trial", "Identical body text here.")
    assert content_hash_for(a) != content_hash_for(b)


def test_different_body_changes_hash() -> None:
    a = _doc("Same title", "First body.")
    b = _doc("Same title", "Second body.")
    assert content_hash_for(a) != content_hash_for(b)


def test_title_body_boundary_prevents_collision() -> None:
    # "AB" + "C" must not collide with "A" + "BC": the NUL separator prevents it.
    a = _doc("AB", "C")
    b = _doc("A", "BC")
    assert content_hash_for(a) != content_hash_for(b)
