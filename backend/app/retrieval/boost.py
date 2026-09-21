"""Post-rerank boosts — recency and evidence grade, transparently.

At equal relevance a newer, higher-graded source should win. Both boosts are
multiplicative factors applied to the rerank score, and — critically — every
input and factor is recorded on the chunk (``pre_boost``, ``recency_factor``,
``grade_factor``, ``post_boost``) so the UI can show a clinician exactly why a
passage ranked where it did.

Boosts never invent relevance: a passage the reranker scored near zero cannot
leap the list on recency alone. They only break ties and nudge among
comparably-relevant passages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.retrieval.types import RetrievedChunk

# Multiplier per evidence grade at equal relevance (A strongest).
_DEFAULT_GRADE_WEIGHTS: dict[str, float] = {
    "A": 1.00,
    "B": 0.92,
    "C": 0.84,
    "D": 0.76,
}
_UNGRADED_WEIGHT = 0.80


@dataclass(frozen=True, slots=True)
class BoostConfig:
    """How hard each boost pulls. Weight 0 disables a boost entirely."""

    recency_weight: float = 0.15
    grade_weight: float = 0.15
    recency_half_life_years: float = 6.0
    reference_year: int = date.today().year
    grade_weights: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_GRADE_WEIGHTS))


def _recency_factor(publication_date: date | None, config: BoostConfig) -> float:
    """Exponential decay in [~0, 1]: 1.0 this year, 0.5 one half-life ago."""
    if publication_date is None:
        return 0.5  # unknown date → neutral-ish
    age_years = max(0.0, config.reference_year - publication_date.year)
    return float(0.5 ** (age_years / config.recency_half_life_years))


def _grade_factor(evidence_grade: str | None, config: BoostConfig) -> float:
    if evidence_grade is None:
        return _UNGRADED_WEIGHT
    return config.grade_weights.get(evidence_grade, _UNGRADED_WEIGHT)


def apply_boosts(
    chunks: list[RetrievedChunk], *, config: BoostConfig | None = None
) -> list[RetrievedChunk]:
    """Apply recency + evidence-grade boosts and re-sort, transparently.

    The multiplier is ``1 + w_recency*(recency-0.5) + w_grade*(grade-0.5)`` so a
    neutral source (mid recency, mid grade) is unchanged, better-than-neutral
    sources rise, and worse ones fall — bounded by the configured weights.
    """
    resolved = config if config is not None else BoostConfig()
    for chunk in chunks:
        pre = chunk.score
        recency = _recency_factor(chunk.publication_date, resolved)
        grade = _grade_factor(chunk.evidence_grade, resolved)
        multiplier = (
            1.0 + resolved.recency_weight * (recency - 0.5) + resolved.grade_weight * (grade - 0.5)
        )
        chunk.components["pre_boost"] = pre
        chunk.components["recency_factor"] = round(recency, 4)
        chunk.components["grade_factor"] = round(grade, 4)
        chunk.components["boost_multiplier"] = round(multiplier, 4)
        chunk.with_score("post_boost", pre * multiplier)
    chunks.sort(key=lambda c: c.score, reverse=True)
    return chunks
