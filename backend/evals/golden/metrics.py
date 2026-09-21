"""Retrieval metrics (recall@k, precision@k, MRR, nDCG@k) and confidence
calibration (reliability buckets, Brier score, ECE) as pure functions.

Retrieval is scored against the golden set's labelled ids *before* any
generation runs, so a regression can be placed in the layer it came from.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from statistics import mean


def recall_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Hits in the top k over ``min(|relevant|, k)``: the share of the
    relevant set that *could* fit in k that did. A topic with thirty relevant
    papers is scored on whether the top ten are relevant, not on the twenty
    that no top-ten list could hold."""
    gold = set(relevant)
    if not gold or k <= 0:
        return 0.0
    return len(gold & set(ranked[:k])) / min(len(gold), k)


def precision_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    if k <= 0:
        return 0.0
    gold = set(relevant)
    return len(gold & set(ranked[:k])) / k


def reciprocal_rank(ranked: Sequence[str], relevant: Iterable[str]) -> float:
    gold = set(relevant)
    for position, item in enumerate(ranked, start=1):
        if item in gold:
            return 1.0 / position
    return 0.0


def ndcg_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Binary-relevance nDCG: every labelled id is equally relevant."""
    gold = set(relevant)
    if not gold or k <= 0:
        return 0.0
    dcg = sum(
        1.0 / math.log2(position + 1)
        for position, item in enumerate(ranked[:k], start=1)
        if item in gold
    )
    ideal = sum(1.0 / math.log2(position + 1) for position in range(1, min(len(gold), k) + 1))
    return dcg / ideal if ideal else 0.0


@dataclass(slots=True)
class RetrievalScores:
    recall_at_5: float
    recall_at_10: float
    precision_at_5: float
    precision_at_10: float
    mrr: float
    ndcg_at_10: float
    first_relevant_rank: int | None

    @classmethod
    def score(cls, ranked: Sequence[str], relevant: Iterable[str]) -> RetrievalScores:
        gold = list(relevant)
        rank = next((i for i, item in enumerate(ranked, start=1) if item in set(gold)), None)
        return cls(
            recall_at_5=recall_at_k(ranked, gold, 5),
            recall_at_10=recall_at_k(ranked, gold, 10),
            precision_at_5=precision_at_k(ranked, gold, 5),
            precision_at_10=precision_at_k(ranked, gold, 10),
            mrr=reciprocal_rank(ranked, gold),
            ndcg_at_10=ndcg_at_k(ranked, gold, 10),
            first_relevant_rank=rank,
        )


def mean_scores(scores: Sequence[RetrievalScores]) -> dict[str, float | int | None]:
    if not scores:
        return {
            "n": 0,
            "recall_at_5": None,
            "recall_at_10": None,
            "precision_at_5": None,
            "precision_at_10": None,
            "mrr": None,
            "ndcg_at_10": None,
        }
    return {
        "n": len(scores),
        "recall_at_5": round(mean(s.recall_at_5 for s in scores), 4),
        "recall_at_10": round(mean(s.recall_at_10 for s in scores), 4),
        "precision_at_5": round(mean(s.precision_at_5 for s in scores), 4),
        "precision_at_10": round(mean(s.precision_at_10 for s in scores), 4),
        "mrr": round(mean(s.mrr for s in scores), 4),
        "ndcg_at_10": round(mean(s.ndcg_at_10 for s in scores), 4),
    }


# -- calibration -------------------------------------------------------------------------

# What the system *means* by each stated level, as a probability of being
# right. These are the values the Brier score is computed against; the
# calibration loop (evals/golden/calibrate.py) replaces them with the observed
# accuracy per bucket when the two drift apart, and that drift is the signal
# to retune the thresholds in ``app.graph.graph._assess``.
NOMINAL_CONFIDENCE: dict[str, float] = {"high": 0.9, "moderate": 0.65, "low": 0.35}


@dataclass(slots=True)
class CalibrationBucket:
    level: str
    n: int
    predicted: float
    observed: float | None

    @property
    def gap(self) -> float | None:
        return None if self.observed is None else round(self.observed - self.predicted, 4)


@dataclass(slots=True)
class CalibrationReport:
    buckets: list[CalibrationBucket] = field(default_factory=list)
    brier: float | None = None
    ece: float | None = None  # expected calibration error, weighted by bucket size
    n: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "n": self.n,
            "brier": self.brier,
            "ece": self.ece,
            "buckets": [
                {
                    "level": b.level,
                    "n": b.n,
                    "predicted": b.predicted,
                    "observed": b.observed,
                    "gap": b.gap,
                }
                for b in self.buckets
            ],
        }


def calibrate(
    outcomes: Iterable[tuple[str, bool]],
    *,
    nominal: dict[str, float] | None = None,
) -> CalibrationReport:
    """Reliability of stated confidence: ``outcomes`` pairs each answer's
    stated level with whether it turned out right."""
    levels = nominal if nominal is not None else NOMINAL_CONFIDENCE
    rows = [(level, bool(ok)) for level, ok in outcomes if level in levels]
    buckets: list[CalibrationBucket] = []
    for level, predicted in levels.items():
        hits = [ok for lvl, ok in rows if lvl == level]
        observed = round(mean(1.0 if ok else 0.0 for ok in hits), 4) if hits else None
        buckets.append(CalibrationBucket(level, len(hits), predicted, observed))
    if not rows:
        return CalibrationReport(buckets=buckets)
    brier = mean((levels[level] - (1.0 if ok else 0.0)) ** 2 for level, ok in rows)
    ece = sum((b.n / len(rows)) * abs((b.observed or 0.0) - b.predicted) for b in buckets if b.n)
    return CalibrationReport(buckets, round(brier, 4), round(ece, 4), len(rows))


__all__ = [
    "NOMINAL_CONFIDENCE",
    "CalibrationBucket",
    "CalibrationReport",
    "RetrievalScores",
    "calibrate",
    "mean_scores",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
    "reciprocal_rank",
]
