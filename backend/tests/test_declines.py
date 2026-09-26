"""Does a model's answer say its passages do not answer the question?

The three declines are the live demo's own answers (Groq gpt-oss-120b,
2026-09-26), written with the model's typographic hyphens and apostrophes;
each was labelled "high confidence, grade A". The rest are the sentences a
decline check must not mistake for one: findings, conclusions drawn from
cited trials, and caveats.
"""

from __future__ import annotations

from app.graph.declines import declines

NB = "\N{NON-BREAKING HYPHEN}"
APOSTROPHE = "\N{RIGHT SINGLE QUOTATION MARK}"

BETA_BLOCKERS = "Should beta-blockers be used after myocardial infarction?"
VITAMIN_D = "Does vitamin D supplementation prevent fractures?"
ASPIRIN = "Should aspirin be used for primary prevention of cardiovascular disease?"
METFORMIN = "Is metformin first-line therapy for type 2 diabetes?"

LIVE_VITAMIN_D = (
    "The cited studies evaluate vitamin D supplementation primarily in preterm and "
    f"low{NB}birth{NB}weight infants and report outcomes such as deficiency rates, secondary "
    "hyperparathyroidism, and bone mineral content, but they do not provide data on fracture "
    "incidence or on fracture prevention in any population [3][5]. Consequently, the available "
    "evidence in these passages does not address whether vitamin D supplementation prevents "
    "fractures."
)
LIVE_ASPIRIN = (
    "Aspirin use for primary prevention of cardiovascular disease is not supported by direct "
    f"outcome data in the cited literature. One population{NB}based study reported that among "
    f"adults at high estimated 10{NB}year ASCVD risk, only about 30 % were using aspirin, but the "
    "study did not assess whether aspirin reduced cardiovascular events in this "
    f"primary{NB}prevention cohort [2]."
)
LIVE_METFORMIN = (
    "The cited literature does not address the role of metformin as an initial, "
    f"first{NB}line agent for type 2 diabetes; the studies described focus on combination "
    f"regimens or add{NB}on therapies rather than establishing metformin{APOSTROPHE}s position "
    "in the treatment hierarchy. Consequently, no source among the provided passages supports "
    f"a statement that metformin is first{NB}line therapy for type 2 diabetes."
)
LIVE_BETA_BLOCKERS = (
    f"Optimal medical therapy that includes a beta{NB}blocker at hospital discharge is "
    "associated with a lower risk of major adverse cardiovascular events after acute "
    "myocardial infarction treated with percutaneous coronary intervention [1]. Observational "
    "data also indicate that a substantial proportion of patients who experience recurrent "
    f"myocardial infarction are not receiving guideline{NB}recommended therapies, including "
    f"beta{NB}blockers, at the time of rehospitalisation [4]."
)


def test_the_live_demo_declines_are_recognised() -> None:
    assert declines(VITAMIN_D, LIVE_VITAMIN_D)
    assert declines(ASPIRIN, LIVE_ASPIRIN)
    assert declines(METFORMIN, LIVE_METFORMIN)
    assert not declines(BETA_BLOCKERS, LIVE_BETA_BLOCKERS)


def test_ways_of_saying_the_passages_miss_the_question() -> None:
    assert declines(
        METFORMIN,
        "Based on the provided passages, here is what is known. Whether metformin is "
        "first-line therapy for type 2 diabetes is not addressed in the provided passages.",
    )
    assert declines(
        VITAMIN_D,
        "None of the retrieved studies evaluates vitamin D supplementation for fracture "
        "prevention.",
    )
    assert declines(
        METFORMIN,
        f"The provided passages don{APOSTROPHE}t contain information about metformin as "
        f"first{NB}line therapy for type 2 diabetes.",
    )
    assert declines(
        VITAMIN_D,
        "There is no data on fractures from vitamin D supplementation in these passages.",
    )


def test_findings_and_conclusions_are_not_declines() -> None:
    """The passages must be what lacks something, and what they lack must be
    coverage: a trial that did not reduce events is a result."""
    assert not declines(
        ASPIRIN,
        "Aspirin did not reduce cardiovascular events in the cited primary prevention "
        "trials [2][3].",
    )
    assert not declines(
        ASPIRIN,
        "The cited trials did not show a benefit of aspirin for primary prevention of "
        "cardiovascular disease [2].",
    )
    assert not declines(
        VITAMIN_D,
        "No trial in the cited literature showed that vitamin D supplementation prevented "
        "fractures [1][2].",
    )
    assert not declines(
        ASPIRIN,
        "The available evidence does not support routine aspirin for primary prevention of "
        "cardiovascular disease [2][4].",
    )
    # "Not supported by" the literature is a conclusion when it cites the
    # trials it was drawn from, and a decline only when nothing is cited.
    assert not declines(
        ASPIRIN,
        "Routine aspirin for primary prevention of cardiovascular disease is not supported by "
        "the cited trials, which found that bleeding offset the reduction in events [2][3].",
    )


def test_caveats_are_not_declines() -> None:
    """Off the question's topic, or after an answer has been given, a
    sentence about what the passages lack is a caveat."""
    assert not declines(BETA_BLOCKERS, "The cited trials did not include patients over 80 [3].")
    assert not declines(
        METFORMIN,
        "Metformin is first-line therapy for type 2 diabetes [1]. The provided passages do not "
        "address metformin in type 2 diabetes with advanced kidney disease.",
    )
    assert not declines(
        BETA_BLOCKERS,
        "Beta-blockers reduce mortality after myocardial infarction [1][2]. The cited trials "
        "did not evaluate beta-blockers after myocardial infarction in patients with preserved "
        "ejection fraction [3].",
    )
