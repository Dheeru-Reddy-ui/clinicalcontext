"""Data shapes flowing through the ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

SourceType = Literal["pubmed", "pmc", "guideline", "uploaded"]

StudyType = Literal[
    "systematic_review",
    "meta_analysis",
    "randomized_controlled_trial",
    "cohort_study",
    "case_control_study",
    "case_series",
    "case_report",
    "clinical_guideline",
    "narrative_review",
    "other",
]

EvidenceGrade = Literal["A", "B", "C", "D"]

ClassificationMethod = Literal["publication_types", "llm", "unclassified"]


@dataclass(slots=True)
class RawSection:
    """One titled span of document text (an abstract label, a JATS <sec>,
    a PDF heading's span)."""

    title: str | None
    content: str


@dataclass(slots=True)
class RawDocument:
    """A source-parsed document, ready for classification and chunking."""

    source_type: SourceType
    title: str
    sections: list[RawSection]
    external_id: str | None = None
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    journal: str | None = None
    publication_date: date | None = None
    doi: str | None = None
    pmid: str | None = None
    url: str | None = None
    publication_types: list[str] = field(default_factory=list)
    mesh_terms: list[str] = field(default_factory=list)
    storage_path: str | None = None

    def full_text(self) -> str:
        return "\n\n".join(section.content for section in self.sections if section.content)


@dataclass(slots=True)
class Classification:
    """A study-type decision with its provenance — the reasoning is stored,
    never just the grade."""

    study_type: StudyType | None
    evidence_grade: EvidenceGrade | None
    method: ClassificationMethod
    reasoning: str
    signals: list[str] = field(default_factory=list)
    model: str | None = None
    prompt_version: str | None = None


@dataclass(slots=True)
class Chunk:
    chunk_index: int
    content: str
    token_count: int
    section: str | None
    strategy: str = "structural"
    # What actually gets embedded. None → embed `content`. The structural
    # strategy sets this to "{title}\n{section}\n{content}" so the vector
    # carries context the raw passage lacks, while the citation the user sees
    # stays the clean `content`.
    embed_text: str | None = None

    @property
    def embedding_input(self) -> str:
        return self.embed_text if self.embed_text is not None else self.content


@dataclass(slots=True)
class IngestStats:
    """Pipeline outcome for one run. All counters are per-run, not global."""

    source: str
    query: str | None = None
    fetched: int = 0
    inserted: int = 0
    deduplicated: int = 0
    skipped_no_text: int = 0
    failed: int = 0
    chunks_written: int = 0
    embedded: int = 0
    embedding_pending: int = 0

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            "source": self.source,
            "query": self.query,
            "fetched": self.fetched,
            "inserted": self.inserted,
            "deduplicated": self.deduplicated,
            "skipped_no_text": self.skipped_no_text,
            "failed": self.failed,
            "chunks_written": self.chunks_written,
            "embedded": self.embedded,
            "embedding_pending": self.embedding_pending,
        }
