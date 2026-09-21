"""One golden item: a clinical question with its expert-derived ground truth.

Provenance is part of the record. A corpus-derived item names the PubMed
document whose authors' conclusion is the reference answer and the MeSH
headings (PubMed's human indexing) that selected the relevant documents; a
promoted item names the reviewer and the feedback it grew from.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "therapy",
    "harm",
    "first_line",
    "guideline",
    "diagnosis",
    "prognosis",
    "comparison",
    "abstain",
]
Grade = Literal["A", "B", "C", "D"]

SET_PATH = Path(__file__).resolve().parent / "set.jsonl"


class ReferenceSource(BaseModel):
    """Where the reference answer comes from — the authors' own conclusion."""

    document_id: str
    chunk_id: str | None = None
    pmid: str | None = None
    title: str
    journal: str | None = None
    year: int | None = None
    study_type: str | None = None
    evidence_grade: Grade | None = None


class Provenance(BaseModel):
    source: Literal["corpus_derived", "feedback"]
    built_at: str
    corpus_signature: str | None = None
    # corpus_derived: the MeSH headings that selected the relevant documents.
    mesh_terms: list[str] = Field(default_factory=list)
    # feedback: who reviewed the case and which feedback row it grew from.
    feedback_id: str | None = None
    reviewed_by: str | None = None
    review_note: str | None = None


class GoldenItem(BaseModel):
    id: str
    question: str
    category: Category
    # The Ely et al. (1999) generic question the item instantiates.
    pattern: str
    reference_answer: str
    reference_source: ReferenceSource | None = None
    relevant_document_ids: list[str] = Field(default_factory=list)
    relevant_chunk_ids: list[str] = Field(default_factory=list)
    expected_grade: Grade | None = None
    contradiction_expected: bool = False
    expected_abstain: bool = False
    pico: dict[str, str] | None = None
    provenance: Provenance

    @property
    def pico_eligible(self) -> bool:
        return self.pico is not None


def load_set(path: Path = SET_PATH) -> list[GoldenItem]:
    items: list[GoldenItem] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                items.append(GoldenItem.model_validate_json(line))
    return items


def append_item(item: GoldenItem, path: Path = SET_PATH) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n")


def write_set(items: list[GoldenItem], path: Path = SET_PATH) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n")


__all__ = [
    "SET_PATH",
    "Category",
    "GoldenItem",
    "Provenance",
    "ReferenceSource",
    "append_item",
    "load_set",
    "write_set",
]
