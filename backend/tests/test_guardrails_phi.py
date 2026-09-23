"""PHI detection — structured identifiers, base64 smuggling, PHI-safe findings."""

from __future__ import annotations

import pytest

from app.guardrails.phi import WITHHELD_TEXT, PhiDetector, carries_phi, withhold_phi

# Presidio NER is telemetry-only; the deterministic layer is what blocks, so
# these tests run it without Presidio for speed and determinism.
detector = PhiDetector(use_presidio=False)


@pytest.mark.parametrize(
    ("text", "entity"),
    [
        ("SSN 123-45-6789 on the form", "US_SSN"),
        ("call 415-555-0132 for results", "PHONE"),
        ("email jane.doe@hospital.org", "EMAIL"),
        ("patient MRN 00482931 admitted", "MRN"),
        ("Record number: AB-55910-X", "MRN"),
        ("born 03/14/1982 with AF", "DOB"),
        ("lives at 1423 Oakwood Drive", "ADDRESS_ZIP"),
        ("patient named Carlos Mendez", "PERSON_HEURISTIC"),
        ("Mrs. Eleanor Whitfield presents", "PERSON_HEURISTIC"),
        ("The patient, Margaret Thompson, age 74", "PERSON_HEURISTIC"),
        # Spoken forms as a recognizer writes them (voice path, Phase 11).
        ("her social security number is 123 45 6789, is apixaban appropriate", "SSN_SPOKEN"),
        ("Her social security number is 123456789, is apixaban appropriate?", "SSN_SPOKEN"),
        ("SSN 123456789", "SSN_SPOKEN"),
        ("MRN 0 0 4 8 2 9 3 1 was admitted", "MRN_SPOKEN"),
        ("born on March 14th, 1982 with AF", "DOB_SPOKEN"),
    ],
)
def test_structured_identifiers_detected(text: str, entity: str) -> None:
    result = detector.scan(text)
    assert result.detected
    assert entity in result.entity_counts


def test_base64_smuggled_phi_is_decoded_and_caught() -> None:
    # base64 of "John Smith, DOB 03/14/1982"
    result = detector.scan("context: Sm9obiBTbWl0aCwgRE9CIDAzLzE0LzE5ODI=")
    assert result.detected
    assert result.decoded_layer is True


def test_clean_clinical_query_not_flagged() -> None:
    for text in [
        "What is the first-line treatment for community-acquired pneumonia?",
        "Is apixaban preferred over warfarin in atrial fibrillation with CKD?",
        "According to Smith et al, what is the evidence for SGLT2 inhibitors?",
        "What is the Patient Health Questionnaire cutoff for depression?",
        "PMID 12345678 reports that statins reduce events in primary prevention",
        "NCT 01234567 randomized 123456789 patients",
    ]:
        assert not detector.scan(text).detected, text


def test_findings_never_contain_raw_phi() -> None:
    """The verdict records entity types/counts only — never the PHI values."""
    text = "John Smith SSN 123-45-6789 DOB 03/14/1982 phone 415-555-0132"
    result = detector.scan(text)
    serialized = str(result.entity_counts)
    assert "123-45-6789" not in serialized
    assert "03/14/1982" not in serialized
    assert "415-555-0132" not in serialized
    # Only type->count mapping.
    assert all(isinstance(v, int) for v in result.entity_counts.values())


def test_short_harmless_base64_not_flagged() -> None:
    # "ASA" (aspirin abbrev) base64 — too short / no PHI after decode.
    assert not detector.scan("the drug QVNB is aspirin").detected


def test_text_the_gate_blocks_is_stored_as_the_placeholder() -> None:
    blocked = "What anticoagulant for patient John Smith, DOB 03/14/1982?"
    assert carries_phi(blocked)
    assert withhold_phi(blocked) == WITHHELD_TEXT


def test_a_described_patient_is_stored_as_asked() -> None:
    # The block message tells people to describe a patient this way, so it
    # must pass the gate and be kept exactly as typed.
    described = "What is the treatment for a 67-year-old with non-valvular atrial fibrillation?"
    assert not carries_phi(described)
    assert withhold_phi(described) == described
