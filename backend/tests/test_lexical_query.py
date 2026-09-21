"""Synonym-expanded query variant + token builders (no DB)."""

from __future__ import annotations

from app.retrieval.lexical import build_query_variants, query_tokens

_SYNONYMS = {
    "afib": ["atrial fibrillation"],
    "mi": ["myocardial infarction"],
    "ckd": ["chronic kidney disease"],
}


def test_original_query_is_always_first_variant() -> None:
    variants = build_query_variants("afib management", _SYNONYMS)
    assert variants[0] == "afib management"


def test_matched_abbreviation_produces_an_expanded_variant() -> None:
    variants = build_query_variants("afib anticoagulation", _SYNONYMS)
    assert "atrial fibrillation anticoagulation" in variants


def test_multiple_abbreviations_expand_independently() -> None:
    variants = build_query_variants("afib in ckd", _SYNONYMS)
    assert "atrial fibrillation in ckd" in variants
    assert "afib in chronic kidney disease" in variants


def test_whole_word_matching_only() -> None:
    # "af" is not a synonym here and "mi" must not fire inside "administration".
    variants = build_query_variants("drug administration schedule", _SYNONYMS)
    assert variants == ["drug administration schedule"]


def test_no_synonyms_returns_just_the_query() -> None:
    assert build_query_variants("pneumonia treatment", _SYNONYMS) == ["pneumonia treatment"]


def test_variants_are_capped_and_unique() -> None:
    many = {f"ab{i}": [f"expansion {i}"] for i in range(20)}
    query = " ".join(f"ab{i}" for i in range(20))
    variants = build_query_variants(query, many)
    assert len(variants) <= 6
    assert len(variants) == len(set(variants))


def test_query_tokens_include_expansion_terms() -> None:
    tokens = query_tokens("afib anticoagulation", _SYNONYMS)
    # Original abbreviation plus the expanded phrase's tokens, all present.
    assert "afib" in tokens
    assert "atrial" in tokens
    assert "fibrillation" in tokens
    assert "anticoagulation" in tokens


def test_query_tokens_are_unique_and_drop_single_chars() -> None:
    tokens = query_tokens("a MI in the heart heart", {"mi": ["myocardial infarction"]})
    assert tokens.count("heart") == 1  # de-duplicated
    assert "a" not in tokens  # single char dropped
    assert "myocardial" in tokens and "infarction" in tokens
