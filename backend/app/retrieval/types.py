"""Shared retrieval types carried through every stage of the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID


@dataclass(slots=True)
class RetrievedChunk:
    """A chunk plus everything downstream stages need.

    ``score`` is the value from the most recent stage; ``components`` records
    each stage's contribution (dense/lexical/fused/rerank + boost factors) so
    the UI can explain exactly why a passage ranked where it did.
    """

    chunk_id: UUID
    document_id: UUID
    content: str
    section: str | None
    title: str | None
    publication_date: date | None
    evidence_grade: str | None
    study_type: str | None
    # Source identifiers, so a citation can link straight out to the paper.
    journal: str | None = None
    pmid: str | None = None
    doi: str | None = None
    url: str | None = None
    score: float = 0.0
    components: dict[str, float] = field(default_factory=dict)

    def with_score(self, stage: str, score: float) -> RetrievedChunk:
        """Record a stage's score and make it the current score (in place)."""
        self.components[stage] = score
        self.score = score
        return self


@dataclass(slots=True)
class StageTiming:
    stage: str
    duration_ms: float
    result_count: int
