"""Phase 12: the retrieval metrics, calibration arithmetic, golden-set
schema and the builder's labelling rules (pure; no DB)."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.golden.build import (
    CAPS,
    Doc,
    _stance_split,
    _supports,
    balance,
)
from evals.golden.metrics import (
    NOMINAL_CONFIDENCE,
    RetrievalScores,
    calibrate,
    mean_scores,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from evals.golden.schema import Category, GoldenItem, Provenance, load_set, write_set


def test_retrieval_metrics_against_worked_examples() -> None:
    ranked = ["a", "x", "b", "y", "z", "c"]
    gold = ["a", "b", "c", "d"]
    assert recall_at_k(ranked, gold, 10) == 0.75
    assert recall_at_k(ranked, gold, 3) == pytest.approx(2 / 3)  # denominator min(4, 3)
    assert recall_at_k(["a", "b"], ["a", "b", "c", "d", "e", "f"], 2) == 1.0
    assert precision_at_k(ranked, gold, 3) == pytest.approx(2 / 3)
    assert reciprocal_rank(ranked, gold) == 1.0
    assert reciprocal_rank(["x", "y", "b"], gold) == pytest.approx(1 / 3)
    assert reciprocal_rank(["x"], gold) == 0.0
    # nDCG@10: hits at ranks 1, 3, 6 against an ideal of four hits at 1-4.
    dcg = 1 / 1 + 1 / 2 + 1 / 2.807354922057604
    idcg = 1 / 1 + 1 / 1.584962500721156 + 1 / 2 + 1 / 2.321928094887362
    assert ndcg_at_k(ranked, gold, 10) == pytest.approx(dcg / idcg, rel=1e-6)
    assert ndcg_at_k(ranked, [], 10) == 0.0 and recall_at_k(ranked, [], 10) == 0.0
    scores = RetrievalScores.score(ranked, gold)
    assert scores.first_relevant_rank == 1 and scores.recall_at_5 == 0.5  # 2 of min(4, 5)
    summary = mean_scores([scores, RetrievalScores.score(["z"], gold)])
    assert summary["n"] == 2 and summary["recall_at_10"] == pytest.approx(0.375)
    assert mean_scores([])["recall_at_10"] is None


def test_calibration_buckets_brier_and_ece() -> None:
    outcomes = [("high", True)] * 9 + [("high", False)] + [("low", False)] * 3 + [("low", True)]
    report = calibrate(outcomes)
    by_level = {b.level: b for b in report.buckets}
    assert by_level["high"].n == 10 and by_level["high"].observed == 0.9
    assert by_level["low"].observed == 0.25 and by_level["moderate"].observed is None
    assert by_level["high"].gap == 0.0 and by_level["low"].gap == pytest.approx(-0.1)
    brier = (9 * (0.9 - 1) ** 2 + (0.9 - 0) ** 2 + 3 * (0.35 - 0) ** 2 + (0.35 - 1) ** 2) / 14
    assert report.brier == pytest.approx(round(brier, 4))
    assert report.ece == pytest.approx(round((10 / 14) * 0.0 + (4 / 14) * 0.1, 4))
    assert calibrate([]).brier is None
    assert set(NOMINAL_CONFIDENCE) == {"high", "moderate", "low"}


def _doc(title: str, conclusion: str, mesh: tuple[str, ...] = ()) -> Doc:
    return Doc(
        id=title[:8],
        title=title,
        journal=None,
        year=2025,
        pmid=None,
        study_type="meta_analysis",
        grade="A",
        mesh=frozenset(mesh),
        conclusion_ids=["c1"],
        conclusion=conclusion,
    )


def test_content_gate_requires_the_entity_and_the_question_type() -> None:
    statins_cvd = _doc(
        "Statins for primary prevention of cardiovascular events: a meta-analysis",
        "Statin therapy reduced major cardiovascular events.",
    )
    tangential = _doc(
        "Ramadan fasting among adolescents with type 1 diabetes",
        "Fasting did not alter HbA1c; hypoglycaemia was frequent.",
    )
    statin_terms = ["hydroxymethylglutaryl-coa reductase inhibitors", "cardiovascular diseases"]
    assert _supports(statins_cvd, "therapy", statin_terms)
    assert not _supports(tangential, "first_line", ["diabetes mellitus, type 1"])
    # A paper *about* guidelines is not the guideline.
    audit = _doc(
        "Evaluating ChatGPT's adherence to evidence-based heart failure guidelines",
        "Concordance with ESC guidelines was moderate.",
    )
    assert not _supports(audit, "guideline", ["heart failure"])
    guideline = _doc(
        "2024 ESC guideline for the management of heart failure",
        "We recommend SGLT2 inhibitors for all patients with HFrEF.",
    )
    assert _supports(guideline, "guideline", ["heart failure"])
    # Comparison words may live in the conclusion.
    head_to_head = _doc(
        "Ticagrelor and clopidogrel in acute coronary syndrome",
        "Ticagrelor reduced mortality compared with clopidogrel.",
    )
    pair = ["ticagrelor", "clopidogrel", "acute coronary syndrome"]
    assert _supports(head_to_head, "comparison", pair)


def test_contradiction_label_needs_both_stances() -> None:
    supports = _doc("a", "Statins are recommended and reduce mortality in primary prevention.")
    against = _doc("b", "There is insufficient evidence; statins should not be used routinely.")
    assert _stance_split([supports, against])
    assert not _stance_split([supports, supports])


def _item(ident: str, category: Category, question: str) -> GoldenItem:
    return GoldenItem(
        id=ident,
        question=question,
        category=category,
        pattern="p",
        reference_answer="r" * 130,
        relevant_document_ids=["d"],
        relevant_chunk_ids=["c"],
        provenance=Provenance(source="corpus_derived", built_at="now"),
    )


def test_balance_caps_each_category_and_dedupes_questions(tmp_path: Path) -> None:
    pool = {
        "therapy": [_item(f"t{i}", "therapy", f"Q{i % 70}?") for i in range(90)],
        "abstain": [_item("a1", "abstain", "Is the moon cheese?")],
    }
    chosen = balance(pool, seed=1)
    therapy = [i for i in chosen if i.category == "therapy"]
    assert len(therapy) == CAPS["therapy"]
    assert len({i.question for i in therapy}) == len(therapy)
    assert [i.id for i in chosen if i.category == "abstain"] == ["abstain-001"]
    path = tmp_path / "set.jsonl"
    write_set(chosen, path)
    assert [i.id for i in load_set(path)] == [i.id for i in chosen]
