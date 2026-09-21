"""Phase 12: the golden runner's pure pieces — the gate, the summary, the
judge protocol with a scripted judge, and the RAGAS context-precision
arithmetic. The runner itself is exercised against the database by the CI
job; these are what make its verdicts trustworthy."""

from __future__ import annotations

from typing import Any

import pytest

from app.schemas.answer import Confidence
from evals.golden.judge import JudgeInput, JudgeScores, context_precision, judge_items
from evals.golden.run import ItemRecord, apply_gate, select_items, summarize
from evals.golden.schema import Category, GoldenItem, Provenance
from tests.test_graph_agent import _chunk


def _golden(ident: str, category: Category, *, abstain: bool = False) -> GoldenItem:
    return GoldenItem(
        id=ident,
        question=f"Question {ident}?",
        category=category,
        pattern="p",
        reference_answer="r" * 130,
        relevant_document_ids=[] if abstain else ["d1"],
        relevant_chunk_ids=[] if abstain else ["c1", "c2"],
        expected_grade=None if abstain else "A",
        contradiction_expected=category == "comparison",
        expected_abstain=abstain,
        provenance=Provenance(source="corpus_derived", built_at="now"),
    )


def _result(*, abstained: bool, confidence: Confidence, contradiction: bool = False) -> Any:
    from app.schemas.answer import AnswerResult, Citation, Contradiction

    chunk = _chunk("Apixaban reduces stroke risk [1].", score=0.7)
    return AnswerResult(
        query="q",
        query_type="therapy",
        is_multi_hop=False,
        abstained=abstained,
        answer="" if abstained else "Apixaban reduces stroke risk [1].",
        citations=[]
        if abstained
        else [
            Citation(
                marker=1,
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                title="Doc",
                section=None,
                publication_date="2020-01-01",
                evidence_grade="A",
                study_type="randomized_controlled_trial",
                passage=chunk.content,
            )
        ],
        contradiction=Contradiction(detected=contradiction),
        confidence=confidence,
        evidence_grade=None if abstained else "A",
        retrieval_grade="irrelevant" if abstained else "sufficient",
        model="heuristic",
    )


def test_summary_counts_abstention_citations_contradictions_and_calibration() -> None:
    answered = ItemRecord(item=_golden("t1", "therapy"))
    answered.result = _result(abstained=False, confidence="high")
    answered.cited_gold = True
    answered.grounding_support = 1.0
    wrong = ItemRecord(item=_golden("c1", "comparison"))
    wrong.result = _result(abstained=False, confidence="high", contradiction=False)
    wrong.cited_gold = False
    over = ItemRecord(item=_golden("t2", "therapy"))
    over.result = _result(abstained=True, confidence="low")
    uncovered = ItemRecord(item=_golden("a1", "abstain", abstain=True))
    uncovered.result = _result(abstained=True, confidence="low")
    leaked = ItemRecord(item=_golden("a2", "abstain", abstain=True))
    leaked.result = _result(abstained=False, confidence="moderate", contradiction=True)

    summary = summarize([answered, wrong, over, uncovered, leaked], {"ran": False})
    g = summary["generation"]
    assert g["abstained_when_answerable"] == round(1 / 3, 4)
    assert g["abstained_when_expected"] == 0.5
    assert g["cited_gold_rate"] == 0.5
    assert g["contradiction"] == {"expected": 1, "detected": 0, "precision": None, "recall": 0.0}
    cal = summary["calibration"]
    assert cal["outcome"] == "cited_gold" and cal["n"] == 2
    high = next(b for b in cal["buckets"] if b["level"] == "high")
    assert high == {"level": "high", "n": 2, "predicted": 0.9, "observed": 0.5, "gap": -0.4}


def test_gate_levels_and_regression() -> None:
    report: dict[str, Any] = {
        "safety": {
            "gate_passed": True,
            "overall_rate": 1.0,
            "categories": {
                "phi_injection": {"total": 28, "passed": 28},
                "diagnosis": {"total": 18, "passed": 18},
            },
        },
        "generation": {
            "blocked": [],
            "abstained_when_expected": 0.9,
            "grounding_support_mean": 0.80,
        },
        "retrieval": {"chunks": {"recall_at_10": 0.20}},
        "judge": {"ran": False},
    }
    assert apply_gate(report, level="safety", baseline=None)["passed"]
    baseline: dict[str, Any] = {
        "retrieval": {"chunks": {"recall_at_10": 0.25}},
        "generation": {"grounding_support_mean": 0.81},
        "judge": {"ran": False},
    }
    gated = apply_gate(report, level="all", baseline=baseline)
    assert "recall_at_10_regression_le_2pts" in gated["failed"], "a 5-point drop fails"
    assert not any("faithfulness" in f for f in gated["failed"]), "a 1-point drop passes"
    assert not gated["passed"]
    # Safety is unconditional: a single PHI miss fails even the safety level.
    report["safety"]["categories"]["phi_injection"]["passed"] = 27
    assert not apply_gate(report, level="safety", baseline=None)["passed"]
    # Judge faithfulness is preferred when both runs carry it.
    report["safety"]["categories"]["phi_injection"]["passed"] = 28
    report["judge"] = {"ran": True, "means": {"faithfulness": 0.70}}
    baseline["judge"] = {"ran": True, "means": {"faithfulness": 0.95}}
    baseline["retrieval"]["chunks"]["recall_at_10"] = 0.20
    judged = apply_gate(report, level="all", baseline=baseline)
    assert judged["failed"] == ["faithfulness_regression_le_2pts (judge)"]


def test_select_items_filters_by_category_id_and_limit() -> None:
    items = [_golden("t1", "therapy"), _golden("h1", "harm"), _golden("t2", "therapy")]
    assert [i.id for i in select_items(items, categories={"therapy"}, ids=None, limit=None)] == [
        "t1",
        "t2",
    ]
    assert [i.id for i in select_items(items, categories=None, ids={"h1"}, limit=None)] == ["h1"]
    assert len(select_items(items, categories=None, ids=None, limit=2)) == 2


def test_context_precision_follows_ragas() -> None:
    assert context_precision([True, False, True]) == pytest.approx((1 / 1 + 2 / 3) / 2, abs=1e-4)
    assert context_precision([False, False]) == 0.0
    assert context_precision([]) is None


class _ScriptedJudge:
    name = "scripted"

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def score(self, item: JudgeInput) -> JudgeScores:
        self.seen.append(item.item_id)
        if item.item_id == "boom":
            raise RuntimeError("provider down")
        accuracy = 5 if item.abstained == item.expected_abstain else 2
        return JudgeScores(
            faithfulness=0.9,
            answer_relevance=0.8,
            context_precision=0.5,
            context_recall=None,
            clinical_accuracy=accuracy,
            citation_correctness=4,
            appropriate_hedging=4,
            appropriate_abstention=5,
            correct=accuracy >= 4,
        )


async def test_judge_items_aggregates_and_survives_a_failed_item() -> None:
    inputs = [
        JudgeInput(
            item_id=i,
            question="q",
            reference_answer="r",
            expected_abstain=False,
            answer="a",
            abstained=False,
            cited_passages=["p"],
            retrieved_passages=[],
        )
        for i in ("ok", "boom", "also")
    ]
    judge = _ScriptedJudge()
    report = await judge_items(judge, inputs)
    assert report["ran"] and report["model"] == "scripted" and report["scored"] == 2
    assert judge.seen == ["ok", "boom", "also"]
    assert report["means"]["faithfulness"] == 0.9 and report["means"]["context_recall"] is None
    assert report["means"]["correct_rate"] == 1.0
    assert set(report["per_item"]) == {"ok", "also"}
