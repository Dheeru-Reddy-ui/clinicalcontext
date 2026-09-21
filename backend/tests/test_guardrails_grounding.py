"""Grounding verifier — claim support, pruning unsupported, abstention."""

from __future__ import annotations

from app.guardrails.grounding import (
    LexicalGroundingVerifier,
    is_clinical_claim,
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
