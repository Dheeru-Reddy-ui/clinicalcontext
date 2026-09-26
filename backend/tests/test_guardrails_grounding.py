"""Grounding verifier — claim support, pruning unsupported, abstention."""

from __future__ import annotations

from app.guardrails.grounding import (
    LexicalGroundingVerifier,
    is_clinical_claim,
    normalize_answer,
    split_sentences,
    verify_grounding,
)

APIXABAN_PASSAGE = (
    "In this randomized trial of patients with atrial fibrillation and stage 4 "
    "chronic kidney disease, apixaban reduced the rate of stroke compared with "
    "warfarin, with 35% fewer major bleeding events."
)


def test_sentence_split_and_clinical_detection() -> None:
    answer = "Here is a summary of the evidence. Apixaban reduced stroke risk [1]."
    sentences = split_sentences(answer)
    assert len(sentences) == 2
    assert not is_clinical_claim(sentences[0])  # framing
    assert is_clinical_claim(sentences[1])  # clinical claim


async def test_supported_claim_passes() -> None:
    verifier = LexicalGroundingVerifier()
    support = await verifier.verify(
        "Apixaban reduced the rate of stroke compared with warfarin [1].",
        [APIXABAN_PASSAGE],
    )
    assert support == "supported"


async def test_fabricated_number_is_not_supported() -> None:
    verifier = LexicalGroundingVerifier()
    support = await verifier.verify(
        "Apixaban reduced major bleeding by 90% compared with warfarin [1].",
        [APIXABAN_PASSAGE],
    )
    # 90% is absent from the passage (which says 35%) → not fully supported.
    assert support in ("partially_supported", "unsupported")


async def test_unsupported_claim_fails() -> None:
    verifier = LexicalGroundingVerifier()
    support = await verifier.verify(
        "Rivaroxaban is the preferred agent for pulmonary embolism in pregnancy [1].",
        [APIXABAN_PASSAGE],
    )
    assert support == "unsupported"


async def test_unsupported_sentences_are_pruned() -> None:
    # Several supported sentences plus one fabricated one, so removing the
    # fabrication stays under the 30% abstention threshold → pruned, not rejected.
    answer = (
        "Apixaban reduced the rate of stroke compared with warfarin [1]. "
        "The trial enrolled patients with atrial fibrillation and stage 4 "
        "chronic kidney disease [1]. "
        "Apixaban was associated with 35% fewer major bleeding events [1]. "
        "Apixaban reduced stroke compared with warfarin in this population [1]. "
        "It also cures cancer [1]."
    )
    result = await verify_grounding(answer, {1: APIXABAN_PASSAGE})
    assert result.accepted
    assert "cancer" not in result.kept_answer
    assert "stroke" in result.kept_answer
    assert len(result.removed_sentences) == 1


async def test_no_citation_clinical_sentence_is_removed() -> None:
    answer = "Apixaban reduces stroke risk substantially in all populations."
    result = await verify_grounding(answer, {1: APIXABAN_PASSAGE})
    # Single clinical claim with no citation → removed → >30% removed → abstain.
    assert not result.accepted
    assert result.finding.code == "grounding_rejected"


async def test_abstains_when_too_much_removed() -> None:
    answer = (
        "Apixaban cures cancer [1]. "
        "It reverses aging in elderly patients [1]. "
        "It also regrows amputated limbs [1]."
    )
    result = await verify_grounding(answer, {1: APIXABAN_PASSAGE})
    assert not result.accepted
    assert result.finding.code == "grounding_rejected"
    assert result.kept_answer == ""
    assert result.removed_fraction > 0.30


async def test_fully_grounded_answer_is_unchanged() -> None:
    answer = "Apixaban reduced the rate of stroke compared with warfarin [1]."
    result = await verify_grounding(answer, {1: APIXABAN_PASSAGE})
    assert result.accepted
    assert result.finding.code == "grounding_ok"
    assert result.kept_answer == answer


REGISTRY_PASSAGE = (
    "In a registry of older adults with atrial fibrillation, apixaban was associated "
    "with less intracranial haemorrhage than warfarin."
)


def test_citations_are_read_in_the_forms_models_write_them() -> None:
    assert normalize_answer("Stroke fell. [1] Bleeding fell.[2]") == (
        "Stroke fell [1]. Bleeding fell [2]."
    )
    assert normalize_answer("Stroke fell [1, 2].") == "Stroke fell [1][2]."
    assert normalize_answer("Stroke fell [1-3].") == "Stroke fell [1][2][3]."
    assert normalize_answer("Stroke fell [Passage 2].") == "Stroke fell [2]."
    lenticular = (
        "Stroke fell \N{LEFT BLACK LENTICULAR BRACKET}2\N{DAGGER}L3-L7"
        "\N{RIGHT BLACK LENTICULAR BRACKET}."
    )
    assert normalize_answer(lenticular) == "Stroke fell [2]."
    # Bracketed numbers that are not citations are left alone.
    assert normalize_answer("CI [1.2-3.4]; n [10,000].") == "CI [1.2-3.4]; n [10,000]."


async def test_a_markdown_answer_with_markers_after_the_stop_is_grounded() -> None:
    """How a model writes: a heading, bullets, each marker after its full
    stop. Read literally, every marker opens the next line, every claim is
    uncited, and the whole answer is withheld."""
    answer = (
        "## Evidence\n"
        "- Apixaban reduced the rate of stroke compared with warfarin. [1]\n"
        "- **Apixaban** was associated with less intracranial haemorrhage "
        "than warfarin. [2]\n"
    )
    result = await verify_grounding(answer, {1: APIXABAN_PASSAGE, 2: REGISTRY_PASSAGE})
    assert result.accepted and result.finding.code == "grounding_ok"
    assert result.kept_answer == (
        "Apixaban reduced the rate of stroke compared with warfarin [1]. "
        "Apixaban was associated with less intracranial haemorrhage than warfarin [2]."
    )


async def test_a_table_is_read_row_by_row() -> None:
    answer = (
        "| Trial | Finding |\n"
        "|---|---|\n"
        "| ARISTOTLE-CKD | apixaban reduced the rate of stroke compared with warfarin [1] |\n"
    )
    result = await verify_grounding(answer, {1: APIXABAN_PASSAGE})
    assert result.accepted
    claims = [v for v in result.sentence_verdicts if v.is_clinical_claim]
    assert [v.cited_markers for v in claims] == [[1]] and claims[0].support == "supported"
