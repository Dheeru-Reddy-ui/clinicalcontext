"""Every cited source gets a colour on the evidence timeline.

Before this, stance existed only for a detected contradiction, so an
ordinary answer drew every source grey (the screenshot that reported it:
five sources, all "Neutral"). These pin the rule that replaced it.
"""

from __future__ import annotations

from uuid import uuid4

from app.schemas.answer import Citation, Contradiction, ContradictionPosition
from app.services.stance import assign_stances, polarity, stances_for


def test_sources_the_answer_cites_support_it_and_uncited_ones_are_background() -> None:
    answer = "Apixaban is recommended first-line [1][2]. It reduces stroke risk [3]."
    assert stances_for(answer, [1, 2, 3, 4]) == {
        1: "supports",
        2: "supports",
        3: "supports",
        4: "neutral",
    }


def test_a_sentence_arguing_against_the_conclusion_marks_its_source_opposing() -> None:
    answer = (
        "Statins are recommended for secondary prevention [1]. "
        "One trial found no significant benefit in patients over 85 [2]."
    )
    assert stances_for(answer, [1, 2]) == {1: "supports", 2: "opposes"}


def test_a_caveat_is_not_opposition() -> None:
    answer = "Metformin is first-line [1]. Avoid it when eGFR is below 30 [2]."
    assert stances_for(answer, [1, 2]) == {1: "supports", 2: "supports"}


def test_a_detected_contradiction_places_its_own_sources() -> None:
    contradiction = Contradiction(
        detected=True,
        positions=[
            ContradictionPosition(stance="supports / recommends", markers=[1]),
            ContradictionPosition(stance="does not support / recommends against", markers=[2]),
        ],
    )
    assert stances_for("A [1]. B [2]. C [3].", [1, 2, 3], contradiction) == {
        1: "supports",
        2: "opposes",
        3: "supports",
    }


def test_unlabelled_positions_lead_with_the_first() -> None:
    contradiction = Contradiction(
        detected=True,
        positions=[
            ContradictionPosition(stance="the 2019 guideline", markers=[1]),
            ContradictionPosition(stance="the 2023 guideline", markers=[2]),
        ],
    )
    assert stances_for("X [1] and Y [2].", [1, 2], contradiction) == {
        1: "supports",
        2: "opposes",
    }


def test_marker_ranges_and_lists_are_read() -> None:
    assert stances_for("Summary [1-3], and [5, 6].", range(1, 7)) == {
        1: "supports",
        2: "supports",
        3: "supports",
        4: "neutral",
        5: "supports",
        6: "supports",
    }


def test_negation_wins_over_the_verb_it_contains() -> None:
    assert polarity("Aspirin is not recommended for primary prevention.") == -1
    assert polarity("Aspirin is recommended after MI.") == 1
    assert polarity("Aspirin was studied in 2019.") == 0


def test_assign_stances_fills_every_citation() -> None:
    citations = [
        Citation(
            marker=i,
            chunk_id=uuid4(),
            document_id=uuid4(),
            title=None,
            section=None,
            publication_date=None,
            evidence_grade=None,
            study_type=None,
            passage="p",
        )
        for i in (1, 2)
    ]
    out = assign_stances("Drug X is effective [1].", citations, None)
    assert [c.stance for c in out] == ["supports", "neutral"]
    assert citations[0].stance is None  # inputs untouched


def test_a_markdown_answer_is_judged_bullet_by_bullet() -> None:
    # The live scrub-typhus answer: one "not recommended" bullet about
    # rifampicin turned every source in the list red.
    answer = (
        "**Bottom line:** Doxycycline is the preferred first-line antibiotic [1][5].\n\n"
        "**Recommendation**\n"
        "- **First-line:** Doxycycline is preferred based on the lowest failure rates [1][5].\n"
        "- **Alternative:** Azithromycin is effective when doxycycline is not tolerated [2].\n"
        "- **Not first-line:** Rifampicin is not recommended as initial therapy [3].\n"
    )
    assert stances_for(answer, [1, 2, 3, 4, 5]) == {
        1: "supports",
        2: "supports",
        3: "opposes",
        4: "neutral",
        5: "supports",
    }


def test_a_source_cited_for_the_conclusion_and_a_caveat_still_supports() -> None:
    answer = "Drug A is recommended [1]. Drug B is not recommended [1][2]."
    assert stances_for(answer, [1, 2]) == {1: "supports", 2: "opposes"}
