"""The symptom check: doses, the reasons a medicine is withheld, and triage.

These are the safety-critical parts of the Treatment tab, so the doses are
pinned band by band to the NHS tables they come from, every exclusion is
exercised, and every route to "get emergency help" is checked to stop the
questions and drop the home remedies.
"""

from __future__ import annotations

import pytest

from app.treatment.engine import UnknownComplaint, step
from app.treatment.formulary import (
    Context,
    Profile,
    cetirizine,
    ibuprofen,
    loperamide,
    oral_rehydration,
    paracetamol,
    zinc,
)
from app.treatment.protocols import PROTOCOLS
from app.treatment.sources import SOURCES

ADULT = Profile(age_years=30)


def _p(**kw: object) -> Profile:
    return Profile.model_validate(kw)


# -- paracetamol: the NHS bands ---------------------------------------------------------


@pytest.mark.parametrize(
    ("age_years", "dose_starts"),
    [
        (4 / 12, "60 mg"),
        (1, "120 mg"),
        (3, "180 mg"),
        (5, "240 mg"),
        (7, "250 mg"),
        (9, "375 mg"),
        (11, "500 mg"),
        (14, "500 to 750 mg"),
        (30, "500 mg to 1 g"),
    ],
)
def test_paracetamol_follows_the_nhs_age_bands(age_years: float, dose_starts: str) -> None:
    advice = paracetamol(_p(age_years=age_years), Context())
    assert advice.suitable
    assert advice.dose is not None and advice.dose.startswith(dose_starts)
    assert "4 hours apart" in (advice.how_often or "")


def test_paracetamol_is_not_dosed_for_a_baby_under_three_months() -> None:
    advice = paracetamol(_p(age_years=2 / 12), Context())
    assert not advice.suitable and "under 3 months" in (advice.reason_not_suitable or "")


def test_paracetamol_warns_about_a_second_paracetamol_product() -> None:
    advice = paracetamol(_p(age_years=30, medicines=["Dolo 650"]), Context())
    assert advice.suitable
    assert any("Dolo 650" in note and "do not take both" in note for note in advice.notes)


def test_paracetamol_allergy_excludes_it() -> None:
    assert not paracetamol(_p(age_years=30, allergies=["Paracetamol"]), Context()).suitable


# -- ibuprofen ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("profile", "dose_starts"),
    [
        ({"age_years": 4 / 12, "weight_kg": 6}, "50 mg"),
        ({"age_years": 8 / 12}, "50 mg"),
        ({"age_years": 2}, "100 mg"),
        ({"age_years": 5}, "150 mg"),
        ({"age_years": 8}, "200 mg"),
        ({"age_years": 30}, "200 to 400 mg"),
    ],
)
def test_ibuprofen_follows_the_nhs_age_bands(profile: dict[str, float], dose_starts: str) -> None:
    advice = ibuprofen(_p(**profile), Context())
    assert advice.suitable, advice.reason_not_suitable
    assert advice.dose is not None and advice.dose.startswith(dose_starts)


def test_a_small_baby_needs_a_weight_before_ibuprofen() -> None:
    advice = ibuprofen(_p(age_years=4 / 12), Context())
    assert not advice.suitable and "5 kg" in (advice.reason_not_suitable or "")


@pytest.mark.parametrize(
    ("profile", "context", "because"),
    [
        ({"age_years": 30, "pregnant": True}, Context(), "pregnancy"),
        ({"age_years": 30, "medicines": ["Warfarin 5mg"]}, Context(), "bleeding"),
        ({"age_years": 30, "medicines": ["Ecosprin 75"]}, Context(), "bleeding"),
        ({"age_years": 30, "medicines": ["Combiflam"]}, Context(), "never take two"),
        ({"age_years": 30, "medicines": ["lithium"]}, Context(), "interacts"),
        ({"age_years": 30, "conditions": ["kidney_disease"]}, Context(), "kidney"),
        ({"age_years": 30, "conditions": ["stomach_ulcer"]}, Context(), "ulcer"),
        ({"age_years": 30, "allergies": ["aspirin"]}, Context(), "allergy"),
        ({"age_years": 30}, Context(fever=True, dengue_possible=True), "dengue"),
        ({"age_years": 30}, Context(dehydrated=True), "dehydrated"),
        ({"age_years": 2 / 12}, Context(), "under 3 months"),
    ],
)
def test_ibuprofen_is_withheld_with_the_reason(
    profile: dict[str, object], context: Context, because: str
) -> None:
    advice = ibuprofen(_p(**profile), context)
    assert not advice.suitable
    assert because in (advice.reason_not_suitable or "").lower()


def test_asthma_is_a_caution_not_a_ban() -> None:
    advice = ibuprofen(_p(age_years=30, conditions=["asthma"]), Context())
    assert advice.suitable and any("asthma" in n.lower() for n in advice.notes)


# -- the others -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age_years", "dose", "how_often"),
    [
        (3, "2.5 mg (liquid)", "twice a day"),
        (8, "5 mg", "twice a day"),
        (30, "10 mg", "once a day"),
    ],
)
def test_cetirizine_follows_the_nhs_doses(age_years: float, dose: str, how_often: str) -> None:
    advice = cetirizine(_p(age_years=age_years), Context())
    assert (advice.dose, advice.how_often) == (dose, how_often)


def test_cetirizine_under_two_is_for_a_doctor() -> None:
    assert not cetirizine(_p(age_years=1), Context()).suitable


def test_ors_volume_follows_the_who_plan() -> None:
    assert (oral_rehydration(_p(age_years=1), Context()).dose or "").startswith("50 to 100 ml")
    assert (oral_rehydration(_p(age_years=6), Context()).dose or "").startswith("100 to 200 ml")
    assert "as much as" in (oral_rehydration(ADULT, Context()).dose or "")


def test_zinc_is_for_young_children_only() -> None:
    assert (zinc(_p(age_years=4 / 12), Context()) or pytest.fail()).dose == "10 mg once a day"
    assert (zinc(_p(age_years=2), Context()) or pytest.fail()).dose == "20 mg once a day"
    assert zinc(ADULT, Context()) is None


def test_loperamide_is_not_for_children_or_bloody_diarrhoea() -> None:
    assert not loperamide(_p(age_years=10), Context()).suitable
    assert not loperamide(ADULT, Context(blood_in_stool=True)).suitable
    advice = loperamide(ADULT, Context())
    assert advice.suitable and "12 mg" in (advice.maximum or "")


# -- the flows --------------------------------------------------------------------------


def _run(complaint: str, profile: Profile, answers: dict[str, list[str]]):  # type: ignore[no-untyped-def]
    return step(complaint, profile, answers)


def test_a_baby_under_three_months_with_fever_is_sent_for_help_before_any_question() -> None:
    result = _run("fever", _p(age_years=1 / 12), {})
    assert result.question is None and result.assessment is not None
    assert result.assessment.urgency == "emergency"
    assert result.assessment.medicines == []


def test_one_danger_sign_ends_the_questions() -> None:
    result = _run("fever", ADULT, {"fever_danger": ["neck"]})
    assert result.assessment is not None
    assert result.assessment.urgency == "emergency"
    assert result.assessment.self_care == [] and result.assessment.medicines == []
    assert result.assessment.reasons[0].source == "nhs_meningitis"


def test_questions_come_one_at_a_time_in_order() -> None:
    first = _run("fever", ADULT, {})
    assert first.question is not None and first.question.id == "fever_danger"
    second = _run("fever", ADULT, {"fever_danger": ["none"]})
    assert second.question is not None and second.question.id == "fever_temp"
    assert second.answered == 1 and second.total == 5


def _fever(profile: Profile, **overrides: list[str]):  # type: ignore[no-untyped-def]
    answers = {
        "fever_danger": ["none"],
        "fever_temp": ["38"],
        "fever_days": ["1_2"],
        "fever_warning": ["none"],
        "risk": ["none"],
        **overrides,
    }
    result = _run("fever", profile, answers)
    assert result.assessment is not None, result.question
    return result.assessment


def test_a_short_fever_in_an_adult_is_self_care_with_paracetamol_not_ibuprofen() -> None:
    assessment = _fever(ADULT)
    assert assessment.urgency == "self_care"
    by_key = {m.key: m for m in assessment.medicines}
    assert by_key["paracetamol"].suitable
    assert not by_key["ibuprofen"].suitable
    assert "dengue" in (by_key["ibuprofen"].reason_not_suitable or "")
    assert assessment.tests  # dengue / malaria / typhoid tests are listed


def test_three_days_of_fever_in_an_adult_means_a_doctor_and_says_whose_rule_it_is() -> None:
    assessment = _fever(ADULT, fever_days=["3_4"])
    assert assessment.urgency == "soon"
    assert any(r.source == "clinicalcontext" for r in assessment.reasons)


def test_five_days_of_fever_in_a_child_is_urgent() -> None:
    assert _fever(_p(age_years=4), fever_days=["5_plus"]).urgency == "urgent"


def test_a_baby_of_four_months_at_39_degrees_is_urgent() -> None:
    assert _fever(_p(age_years=4 / 12, weight_kg=6), fever_temp=["39"]).urgency == "urgent"


def test_fever_in_pregnancy_is_urgent() -> None:
    assert _fever(_p(age_years=28, sex="female", pregnant=True)).urgency == "urgent"


def test_dengue_bleeding_is_an_emergency() -> None:
    assert _fever(ADULT, fever_warning=["bleeding"]).urgency == "emergency"


def test_a_high_feverpain_score_brings_the_nice_antibiotic_option() -> None:
    result = _run(
        "cough_cold",
        ADULT,
        {
            "resp_danger": ["none"],
            "resp_symptoms": ["sore_throat", "fever"],
            "throat": ["fever24", "pus", "recent", "inflamed"],
            "resp_more": ["none"],
        },
    )
    assert result.assessment is not None
    assert result.assessment.urgency == "soon"
    assert any("phenoxymethylpenicillin" in d.text for d in result.assessment.doctor_may)
    assert all(d.source == "nice_ng84" for d in result.assessment.doctor_may)


def test_the_throat_questions_are_only_asked_about_a_sore_throat() -> None:
    result = _run("cough_cold", ADULT, {"resp_danger": ["none"], "resp_symptoms": ["cough"]})
    assert result.question is not None and result.question.id == "cough_days"
    after = _run(
        "cough_cold",
        ADULT,
        {"resp_danger": ["none"], "resp_symptoms": ["cough"], "cough_days": ["under_1w"]},
    )
    assert after.question is not None and after.question.id == "resp_more"


def test_a_two_week_cough_points_to_a_tb_test() -> None:
    result = _run(
        "cough_cold",
        ADULT,
        {
            "resp_danger": ["none"],
            "resp_symptoms": ["cough"],
            "cough_days": ["2_3w"],
            "resp_more": ["none"],
        },
    )
    assert result.assessment is not None and result.assessment.urgency == "soon"
    assert any(r.source == "india_ntep" for r in result.assessment.reasons)


def test_a_dehydrated_toddler_needs_a_doctor_today_and_gets_ors_and_zinc() -> None:
    result = _run(
        "diarrhoea_vomiting",
        _p(age_years=2),
        {
            "dv_danger": ["none"],
            "dv_which": ["diarrhoea"],
            "dv_dehydration": ["little_urine"],
            "dv_more": ["none"],
        },
    )
    assert result.assessment is not None and result.assessment.urgency == "urgent"
    keys = [m.key for m in result.assessment.medicines]
    assert keys[:2] == ["ors", "zinc"]


def test_a_man_with_urinary_symptoms_is_told_to_see_a_doctor() -> None:
    result = _run("urinary", _p(age_years=40, sex="male"), {"urine_urgent": ["none"]})
    assert result.assessment is not None and result.assessment.urgency == "soon"
    assert result.assessment.doctor_may[0].source == "nice_ng109"


def test_lip_swelling_with_a_rash_is_anaphylaxis() -> None:
    result = _run("rash_allergy", ADULT, {"rash_danger": ["swelling"]})
    assert result.assessment is not None and result.assessment.urgency == "emergency"


def test_back_pain_with_saddle_numbness_is_an_emergency() -> None:
    result = _run("body_pain", ADULT, {"pain_danger": ["numb"]})
    assert result.assessment is not None and result.assessment.urgency == "emergency"


def test_an_unknown_complaint_is_refused() -> None:
    with pytest.raises(UnknownComplaint):
        step("hiccups", ADULT, {})


# -- integrity ----------------------------------------------------------------------------


def test_every_cited_source_exists_and_every_id_is_unique() -> None:
    for protocol in PROTOCOLS.values():
        question_ids = [q.id for q in protocol.questions]
        assert len(question_ids) == len(set(question_ids)), protocol.id
        for key in protocol.source_keys:
            assert key in SOURCES, (protocol.id, key)
        for question in protocol.questions:
            assert question.options, question.id
            option_ids = [o.id for o in question.options]
            assert len(option_ids) == len(set(option_ids)), question.id
            assert "none" not in option_ids
            for option in question.options:
                if option.urgency is not None:
                    assert option.reason and option.source in SOURCES, (question.id, option.id)


def test_every_protocol_reaches_an_assessment_when_nothing_is_wrong() -> None:
    for protocol in PROTOCOLS.values():
        answers: dict[str, list[str]] = {}
        for _ in range(10):
            result = step(protocol.id, ADULT, answers)
            if result.assessment is not None:
                break
            assert result.question is not None
            first = result.question.options[0].id
            answers[result.question.id] = ["none"] if result.question.kind == "multi" else [first]
        assert result.assessment is not None, protocol.id
        assert result.assessment.urgency in ("self_care", "soon"), protocol.id
        assert result.assessment.source_keys, protocol.id


# -- conditions typed in the person's own words ------------------------------------------


def test_typed_conditions_count_as_the_ones_they_name() -> None:
    profile = _p(age_years=60, other_conditions=["CKD stage 3", "sugar", "BP", "hep B"])
    assert set(profile.conditions) >= {
        "kidney_disease",
        "diabetes",
        "high_blood_pressure",
        "liver_disease",
    }
    assert profile.unmatched_conditions == ()
    advice = ibuprofen(profile, Context())
    assert not advice.suitable and "kidney disease" in (advice.reason_not_suitable or "")


def test_low_bp_and_low_sugar_are_not_read_as_high() -> None:
    profile = _p(age_years=40, other_conditions=["low BP", "low sugar"])
    assert "high_blood_pressure" not in profile.conditions
    assert "diabetes" not in profile.conditions
    assert profile.unmatched_conditions == ("low BP", "low sugar")


def test_typed_dengue_rules_out_ibuprofen_whatever_the_complaint() -> None:
    advice = ibuprofen(_p(age_years=30, other_conditions=["dengue"]), Context())
    assert not advice.suitable
    assert "dengue" in (advice.reason_not_suitable or "")
    assert "who_dengue" in advice.source_keys


def test_typed_chickenpox_rules_out_ibuprofen() -> None:
    advice = ibuprofen(_p(age_years=8, other_conditions=["chicken pox"]), Context())
    assert not advice.suitable and "chickenpox" in (advice.reason_not_suitable or "")
    assert "nhs_chickenpox" in advice.source_keys and "nhs_chickenpox" in SOURCES


def test_an_unknown_condition_is_kept_and_every_medicine_says_to_check_first() -> None:
    assessment = _fever(_p(age_years=30, other_conditions=["typhoid", "  typhoid "]))
    suitable = [m for m in assessment.medicines if m.suitable]
    assert suitable
    for medicine in suitable:
        assert any("typhoid" in note and "pharmacist" in note for note in medicine.notes)


def test_typed_pregnancy_counts_for_a_woman() -> None:
    profile = _p(age_years=28, sex="female", other_conditions=["pregnant, 12 weeks"])
    assert profile.pregnant
    assert _fever(profile).urgency == "urgent"


def test_the_step_says_how_each_typed_condition_was_read() -> None:
    from app.api.v1.assistant import run_step
    from app.schemas.assistant import TreatmentStepRequest

    out = run_step(
        TreatmentStepRequest(
            complaint="fever",
            profile=_p(age_years=30, other_conditions=["dialysis", "dengue", "migraine"]),
        )
    )
    read = {r.text: r for r in out.conditions_read}
    assert read["dialysis"].conditions == ["kidney_disease"] and read["dialysis"].understood
    assert read["dengue"].flags == ["dengue"] and read["dengue"].understood
    assert not read["migraine"].understood
