"""Document browse schemas (the corpus explorer)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

DocumentScope = Literal["all", "shared", "private"]


class DocumentSummary(BaseModel):
    id: UUID
    title: str
    journal: str | None
    publication_date: date | None
    source_type: str
    study_type: str | None
    evidence_grade: str | None
    pmid: str | None
    doi: str | None
    url: str | None
    # True for the shared public corpus (org_id IS NULL), false for a private upload.
    shared: bool
    chunk_count: int
    ingested_at: datetime


class DocumentsOut(BaseModel):
    documents: list[DocumentSummary]
    total: int
    limit: int
    offset: int


class DocumentDetail(DocumentSummary):
    abstract: str | None
    authors: list[Any]
    metadata: dict[str, Any]
    classification: dict[str, Any]


class ChunkOut(BaseModel):
    id: UUID
    chunk_index: int
    section: str | None
    content: str
    token_count: int
    strategy: str | None
    embedded: bool


class DocumentChunksOut(BaseModel):
    document_id: UUID
    chunks: list[ChunkOut]
    total: int
    limit: int
    offset: int


class DocumentUploadOut(BaseModel):
    document_id: UUID
    title: str
    chunk_count: int
    embedded_count: int
    deduplicated: bool
