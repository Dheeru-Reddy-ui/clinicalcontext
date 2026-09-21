"""Corpus browse + private document upload.

Browsing is RLS-scoped: a caller sees the shared public corpus plus their own
org's private uploads, never another tenant's. Upload runs the Phase-4
pipeline (parse → classify → chunk → embed) with ``org_id`` pinned to the
caller's organization, so an uploaded PDF is searchable by that tenant only.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from app.config import get_settings
from app.core.deps import DbPool, get_asyncpg_pool, get_redis_client
from app.core.errors import ClinicalContextError, PermissionDeniedError
from app.core.ratelimit import enforce_rate_limit
from app.core.security import CurrentUser
from app.ingestion.classifier import classify_document
from app.ingestion.pipeline import content_hash_for
from app.ingestion.sources.guideline import parse_guideline_pdf
from app.repositories.base import tenant_connection
from app.repositories.corpus import CorpusRepository
from app.repositories.documents import DocumentReadRepository
from app.retrieval.chunking import chunk_sections
from app.retrieval.embed import embed_document_chunks
from app.schemas.documents import (
    DocumentChunksOut,
    DocumentDetail,
    DocumentScope,
    DocumentsOut,
    DocumentUploadOut,
)

logger = structlog.stdlib.get_logger("app.api.documents")

router = APIRouter(prefix="/documents", tags=["documents"])

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class UnsupportedUploadError(ClinicalContextError):
    status_code = 415
    error_code = "unsupported_upload"
    default_message = "Only PDF uploads are supported."


class UploadTooLargeError(ClinicalContextError):
    status_code = 413
    error_code = "upload_too_large"
    default_message = "The uploaded file is too large."


class UnreadableDocumentError(ClinicalContextError):
    status_code = 422
    error_code = "unreadable_document"
    default_message = "No extractable text was found in the PDF."


@router.get("")
async def list_documents(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    scope: DocumentScope = "all",
    search: str | None = None,
    source_type: str | None = None,
    study_type: str | None = None,
    evidence_grade: str | None = None,
) -> DocumentsOut:
    assert user.org_id is not None
    return await DocumentReadRepository(pool).list_documents(
        org_id=user.org_id,
        user_id=user.user_id,
        limit=limit,
        offset=offset,
        scope=scope,
        search=search,
        source_type=source_type,
        study_type=study_type,
        evidence_grade=evidence_grade,
    )


@router.get("/{document_id}")
async def get_document(
    document_id: UUID,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> DocumentDetail:
    assert user.org_id is not None
    return await DocumentReadRepository(pool).get_document(
        org_id=user.org_id, user_id=user.user_id, document_id=document_id
    )


@router.get("/{document_id}/chunks")
async def get_document_chunks(
    document_id: UUID,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentChunksOut:
    """The exact passages retrieval can cite, in order, with embedding status."""
    assert user.org_id is not None
    return await DocumentReadRepository(pool).get_chunks(
        org_id=user.org_id,
        user_id=user.user_id,
        document_id=document_id,
        limit=limit,
        offset=offset,
    )


@router.post("/upload", status_code=201)
async def upload_document(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    redis: Annotated[Any, Depends(get_redis_client)],
    file: Annotated[UploadFile, File(description="A PDF to add to this org's private corpus")],
    title: Annotated[str | None, Form()] = None,
) -> DocumentUploadOut:
    """Ingest a PDF into the caller's **private** corpus."""
    if user.role == "viewer":
        raise PermissionDeniedError("this principal has read-only access and cannot upload")
    assert user.org_id is not None

    filename = file.filename or "upload.pdf"
    if not filename.lower().endswith(".pdf"):
        raise UnsupportedUploadError()
    payload = await file.read()
    if len(payload) > _MAX_UPLOAD_BYTES:
        raise UploadTooLargeError(
            f"file is {len(payload) // 1024 // 1024} MB; the limit is "
            f"{_MAX_UPLOAD_BYTES // 1024 // 1024} MB"
        )

    settings = get_settings()
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / Path(filename).name
        pdf_path.write_bytes(payload)
        try:
            document = parse_guideline_pdf(pdf_path, title=title or Path(filename).stem)
        except ValueError as exc:
            raise UnreadableDocumentError(str(exc)) from exc

    if not document.full_text().strip():
        raise UnreadableDocumentError()
    chunks = chunk_sections(document.sections)
    if not chunks:
        raise UnreadableDocumentError("the PDF produced no chunkable text")

    allow_llm = settings.ai_backend == "cloud"
    classification = await classify_document(document, allow_llm=allow_llm)

    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        document_id = await CorpusRepository().insert_private_document_with_chunks(
            conn,
            org_id=user.org_id,
            document=document,
            content_hash=content_hash_for(document),
            classification=classification,
            chunks=chunks,
        )
    if document_id is None:
        # This org already holds this exact text (migration 023 scopes the
        # content_hash uniqueness per tenant, so a conflict can only ever be
        # with the caller's own corpus or the shared one).
        async with tenant_connection(pool, user.org_id, user.user_id) as conn:
            existing = await conn.fetchrow(
                "SELECT id, title FROM public.documents WHERE content_hash = $1",
                content_hash_for(document),
            )
        if existing is None:  # pragma: no cover — RLS shows the caller its own row
            raise UnreadableDocumentError("this document could not be stored")
        return DocumentUploadOut(
            document_id=existing["id"],
            title=existing["title"],
            chunk_count=0,
            embedded_count=0,
            deduplicated=True,
        )

    embedder = "cohere" if settings.ai_backend == "cloud" else "local"
    embedded = 0
    try:
        embedded = await embed_document_chunks(
            pool, document_id, embedder_name=embedder, redis=redis
        )
    except Exception as exc:
        # Embedding is resumable: the document and its chunks are already
        # persisted, so a provider outage defers search, it does not lose data.
        logger.warning(
            "upload_embedding_deferred",
            document_id=str(document_id),
            error=f"{type(exc).__name__}: {exc}",
        )

    return DocumentUploadOut(
        document_id=document_id,
        title=document.title,
        chunk_count=len(chunks),
        embedded_count=embedded,
        deduplicated=False,
    )
