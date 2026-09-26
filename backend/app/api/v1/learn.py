"""Learn's tools.

- ``/learn/tutor/quiz`` writes a checked quiz or clinical case on a topic
  from the evidence; ``/learn/tutor/attempts`` and ``/learn/tutor/progress``
  keep each learner's own practice record. Lessons with the tutor are chat
  conversations of kind "tutor" (``POST /chat``).
- ``/learn/notes/summarize`` turns a clinical note or report into a short,
  checked summary — identifiers removed first, nothing stored;
  ``/learn/notes/text`` reads the text out of an uploaded report so the
  person can check it before summarizing.
- ``/learn/papers`` uploads, lists, opens and deletes papers; questions to a
  paper are chat conversations of kind "paper" with its ``document_id``.
"""

from __future__ import annotations

import json
import re
import tempfile
from functools import partial
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import anyio
import pdfplumber
import structlog
from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.assistant.chat import ChatAssistant, ChatEvidence
from app.config import get_settings
from app.core.deps import DbPool, get_asyncpg_pool, get_redis_client
from app.core.errors import (
    ClinicalContextError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from app.core.ratelimit import RateLimiter, enforce_rate_limit
from app.core.security import CurrentUser
from app.graph.graph import citation_from_chunk
from app.ingestion.classifier import classify_document
from app.ingestion.pipeline import content_hash_for
from app.knowledge.specialties import get_specialty
from app.learn import progress as practice
from app.learn.notes import MAX_NOTE_CHARS, summarize_note
from app.learn.papers import PAPER_KIND, UnreadablePaperError, paper_metadata, parse_paper_pdf
from app.learn.quiz import QuizUnavailable, write_quiz
from app.llm.chat import ChatModel
from app.repositories.base import tenant_connection
from app.repositories.chat import ChatRepository
from app.repositories.corpus import CorpusRepository
from app.repositories.tenancy import TenancyRepository
from app.retrieval.chunking import chunk_sections
from app.retrieval.embed import embed_document_chunks
from app.schemas.assistant import ChatSessionOut
from app.schemas.learn import (
    AttemptIn,
    AttemptOut,
    NoteSummarizeIn,
    NoteSummaryOut,
    NoteTextOut,
    PaperDeletedOut,
    PaperDetail,
    PaperOut,
    PaperSectionOut,
    ProgressClearedOut,
    ProgressOut,
    QuizOut,
    QuizQuestionOut,
    QuizRequest,
    SummaryPointOut,
    SummarySectionOut,
)
from app.services import cost

logger = structlog.stdlib.get_logger("app.api.learn")

router = APIRouter(prefix="/learn", tags=["learn"])

_QUIZZES_PER_MINUTE = 6
_SUMMARIES_PER_MINUTE = 10
_UPLOADS_PER_MINUTE = 10
_MAX_NOTE_FILE_BYTES = 10 * 1024 * 1024
_MAX_PAPER_BYTES = 25 * 1024 * 1024
_PAGE_SUFFIX = re.compile(r"^(?P<title>.*?)\s*\N{MIDDLE DOT}\s*p\.\s*(?P<page>\d+)$")


class LearnToolUnavailableError(ClinicalContextError):
    status_code = 503
    error_code = "learn_tool_unavailable"
    default_message = "This tool can't run right now. Try again in a minute."


class UnsupportedFileError(ClinicalContextError):
    status_code = 415
    error_code = "unsupported_file"
    default_message = "That kind of file isn't supported here."


class FileTooLargeError(ClinicalContextError):
    status_code = 413
    error_code = "file_too_large"
    default_message = "The file is too large."


class UnreadableFileError(ClinicalContextError):
    status_code = 422
    error_code = "unreadable_file"
    default_message = "No text could be read from this file."


def _writer(user: CurrentUser) -> UUID:
    """The caller's organization, for the tools that write or call a model."""
    if user.role == "viewer":
        raise PermissionDeniedError("this principal has read-only access")
    assert user.org_id is not None
    return user.org_id


async def _limit(redis: Any, key: str, per_minute: int, what: str) -> None:
    ok, retry_after = await RateLimiter(redis).hit(key, per_minute)
    if not ok:
        raise RateLimitError(f"{what} limit of {per_minute}/min reached", retry_after=retry_after)


# -- the AI tutor ------------------------------------------------------------------------


@router.post("/tutor/quiz")
async def tutor_quiz(
    body: QuizRequest,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    redis: Annotated[Any, Depends(get_redis_client)],
) -> QuizOut:
    """A quiz, or a staged clinical case, on a topic — written from the
    evidence and checked question by question."""
    org_id = _writer(user)
    specialty = get_specialty(body.specialty) if body.specialty else None
    if body.specialty and specialty is None:
        raise InvalidRequestError(f"unknown specialty: {body.specialty}")
    await _limit(redis, f"rl:quiz:{user.user_id}", _QUIZZES_PER_MINUTE, "quiz")
    topic = " ".join(body.topic.split())
    with cost.collecting() as collector:
        try:
            evidence: ChatEvidence | None = None
            async for found in ChatAssistant(pool, redis).gather_evidence(
                topic, org_id=org_id, specialty=body.specialty
            ):
                if isinstance(found, ChatEvidence):
                    evidence = found
            quiz = await write_quiz(
                mode=body.mode,
                topic=topic,
                level=body.level,
                count=body.count,
                specialty_name=specialty.name if specialty else None,
                sources=evidence.chunks if evidence else [],
                model=ChatModel(),
            )
        except QuizUnavailable as exc:
            raise LearnToolUnavailableError(str(exc)) from exc
        finally:
            await cost.flush(pool, collector, org_id=org_id)
    return QuizOut(
        mode=quiz.mode,
        topic=quiz.topic,
        level=quiz.level,
        specialty=body.specialty,
        case=quiz.case,
        questions=[
            QuizQuestionOut(
                stem=q.stem,
                options=q.options,
                answer=q.answer,
                explanation=q.explanation,
                sources=q.sources,
                stage=q.stage,
            )
            for q in quiz.questions
        ],
        sources=[citation_from_chunk(i, c) for i, c in enumerate(quiz.sources, start=1)],
        generated_by=quiz.generated_by,
        model=quiz.model,
        dropped=quiz.dropped,
        notices=quiz.notices,
    )


@router.post("/tutor/attempts", status_code=201)
async def record_tutor_attempt(
    body: AttemptIn,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> AttemptOut:
    """One answered question, for the learner's own progress."""
    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        attempt_id = await practice.record_attempt(
            conn, org_id=user.org_id, user_id=user.user_id, attempt=body
        )
    return AttemptOut(id=attempt_id)


@router.get("/tutor/progress")
async def tutor_progress(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> ProgressOut:
    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        return ProgressOut.model_validate(await practice.progress(conn, user_id=user.user_id))


@router.delete("/tutor/progress")
async def clear_tutor_progress(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> ProgressClearedOut:
    """Start the practice record again (your own rows only)."""
    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        return ProgressClearedOut(deleted=await practice.clear(conn, user_id=user.user_id))


# -- the clinical note summarizer ----------------------------------------------------------


@router.post("/notes/summarize")
async def summarize(
    body: NoteSummarizeIn,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    redis: Annotated[Any, Depends(get_redis_client)],
) -> NoteSummaryOut:
    """A clinical note or report as a short summary, every point checked
    against the line it came from. Identifiers are removed before the text
    goes anywhere; neither the note nor the summary is stored."""
    org_id = _writer(user)
    await _limit(redis, f"rl:notes:{user.user_id}", _SUMMARIES_PER_MINUTE, "summary")
    with cost.collecting() as collector:
        try:
            summary = await summarize_note(body.text)
        finally:
            await cost.flush(pool, collector, org_id=org_id)
    return NoteSummaryOut(
        sections=[
            SummarySectionOut(
                heading=s.heading,
                points=[
                    SummaryPointOut(text=p.text, lines=p.lines, support=p.support) for p in s.points
                ],
            )
            for s in summary.sections
        ],
        lines=summary.lines,
        redactions=summary.redactions,
        mode=summary.mode,
        model=summary.model,
        removed=summary.removed,
        notice=summary.notice,
    )


def _pdf_text(path: Path) -> tuple[str, int]:
    with pdfplumber.open(path) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages[:60]]
        return "\n".join(pages), len(pdf.pages)


@router.post("/notes/text")
async def note_text(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    file: Annotated[UploadFile, File(description="A report as PDF or plain text")],
) -> NoteTextOut:
    """The text of an uploaded report, for the person to check (and remove
    anything they should not share) before it is summarized. Read in memory
    and returned — never stored."""
    _writer(user)
    name = (file.filename or "").lower()
    payload = await file.read(_MAX_NOTE_FILE_BYTES + 1)
    if len(payload) > _MAX_NOTE_FILE_BYTES:
        raise FileTooLargeError("the report is larger than 10 MB")
    pages: int | None = None
    if name.endswith(".pdf"):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.pdf"
            path.write_bytes(payload)
            try:
                text, pages = await anyio.to_thread.run_sync(_pdf_text, path)
            except Exception as exc:
                raise UnreadableFileError("This file couldn't be read as a PDF.") from exc
    elif name.endswith((".txt", ".text", ".md")):
        text = payload.decode("utf-8", errors="replace")
    else:
        raise UnsupportedFileError("Upload a PDF or a plain-text (.txt) file.")
    text = text.strip()
    if not text:
        raise UnreadableFileError(
            "No text could be read — a scanned report is an image; type or paste the text instead."
        )
    return NoteTextOut(
        text=text[:MAX_NOTE_CHARS],
        pages=pages,
        characters=len(text),
        truncated=len(text) > MAX_NOTE_CHARS,
    )


# -- Ask-this-Paper -------------------------------------------------------------------------


async def _paper_out(
    conn: Any, *, document_id: UUID, user: CurrentUser, deduplicated: bool = False
) -> tuple[PaperOut, dict[str, Any]]:
    row = await conn.fetchrow(
        """
        SELECT d.id, d.title, d.abstract, d.study_type::text AS study_type,
               d.evidence_grade::text AS evidence_grade, d.ingested_at, d.metadata,
               (SELECT count(*) FROM public.chunks c WHERE c.document_id = d.id) AS chunk_count
        FROM public.documents d
        WHERE d.id = $1 AND d.org_id = $2
        """,
        document_id,
        user.org_id,
    )
    if row is None:
        raise NotFoundError("paper not found")
    metadata = row["metadata"] if isinstance(row["metadata"], dict) else {}
    if isinstance(row["metadata"], str):
        metadata = json.loads(row["metadata"])
    pages = metadata.get("pages")
    return (
        PaperOut(
            id=row["id"],
            title=row["title"] or "Untitled paper",
            pages=int(pages) if isinstance(pages, int | str) and str(pages).isdigit() else None,
            chunk_count=int(row["chunk_count"]),
            study_type=row["study_type"],
            evidence_grade=row["evidence_grade"],
            uploaded_at=row["ingested_at"],
            uploaded_by_you=metadata.get("uploaded_by") == str(user.user_id),
            deduplicated=deduplicated,
        ),
        {"abstract": row["abstract"], "metadata": metadata},
    )


@router.post("/papers", status_code=201)
async def upload_paper(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    redis: Annotated[Any, Depends(get_redis_client)],
    file: Annotated[UploadFile, File(description="A research paper as PDF")],
    title: Annotated[str | None, Form(max_length=300)] = None,
) -> PaperOut:
    """Read a paper page by page and keep it in the workspace's private
    library, ready for questions."""
    org_id = _writer(user)
    await _limit(redis, f"rl:paper:{user.user_id}", _UPLOADS_PER_MINUTE, "upload")
    filename = Path(file.filename or "paper.pdf").name
    if not filename.lower().endswith(".pdf"):
        raise UnsupportedFileError("Upload the paper as a PDF.")
    payload = await file.read(_MAX_PAPER_BYTES + 1)
    if len(payload) > _MAX_PAPER_BYTES:
        raise FileTooLargeError("the paper is larger than 25 MB")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "paper.pdf"
        path.write_bytes(payload)
        try:
            document, pages = await anyio.to_thread.run_sync(
                partial(parse_paper_pdf, path, title=(title or "").strip() or None)
            )
        except UnreadablePaperError as exc:
            raise UnreadableFileError(str(exc)) from exc
        except Exception as exc:
            raise UnreadableFileError("This file couldn't be read as a PDF.") from exc
    document.external_id = filename
    chunks = chunk_sections(document.sections)
    if not chunks:
        raise UnreadableFileError("No readable passages were found in this PDF.")
    settings = get_settings()
    classification = await classify_document(document, allow_llm=settings.ai_backend == "cloud")
    content_hash = content_hash_for(document)
    async with tenant_connection(pool, org_id, user.user_id) as conn:
        document_id = await CorpusRepository().insert_private_document_with_chunks(
            conn,
            org_id=org_id,
            document=document,
            content_hash=content_hash,
            classification=classification,
            chunks=chunks,
            metadata=paper_metadata(uploaded_by=user.user_id, pages=pages, filename=filename),
        )
        deduplicated = document_id is None
        if document_id is None:
            # The workspace already holds this exact text: that copy answers.
            existing = await conn.fetchval(
                "SELECT id FROM public.documents WHERE content_hash = $1 AND org_id = $2",
                content_hash,
                org_id,
            )
            if existing is None:  # pragma: no cover — RLS shows the caller its own rows
                raise UnreadableFileError("This paper could not be stored.")
            document_id = UUID(str(existing))
            await conn.execute(
                "UPDATE public.documents SET metadata = metadata || $2::jsonb "
                "WHERE id = $1 AND coalesce(metadata ->> 'kind', '') <> $3",
                document_id,
                json.dumps({"kind": PAPER_KIND, "pages": pages}),
                PAPER_KIND,
            )
    if not deduplicated:
        try:
            await embed_document_chunks(
                pool,
                document_id,
                embedder_name="cohere" if settings.ai_backend == "cloud" else "local",
                redis=redis,
            )
        except Exception as exc:  # search by words still works; meaning catches up
            logger.warning("paper_embedding_deferred", error=type(exc).__name__)
    async with tenant_connection(pool, org_id, user.user_id) as conn:
        out, _ = await _paper_out(
            conn, document_id=document_id, user=user, deduplicated=deduplicated
        )
    return out


@router.get("/papers")
async def list_papers(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> list[PaperOut]:
    """The workspace's papers, newest first."""
    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        ids = await conn.fetch(
            "SELECT id FROM public.documents WHERE org_id = $1 AND metadata ->> 'kind' = $2 "
            "ORDER BY ingested_at DESC LIMIT 100",
            user.org_id,
            PAPER_KIND,
        )
        return [(await _paper_out(conn, document_id=r["id"], user=user))[0] for r in ids]


@router.get("/papers/{document_id}")
async def get_paper(
    document_id: UUID,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> PaperDetail:
    """A paper, its outline (section and page), and your conversations about it."""
    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        out, extra = await _paper_out(conn, document_id=document_id, user=user)
        sections = await conn.fetch(
            "SELECT section, min(chunk_index) AS first FROM public.chunks "
            "WHERE document_id = $1 GROUP BY section ORDER BY first",
            document_id,
        )
        sessions = await ChatRepository().list_sessions(
            conn, user_id=user.user_id, kinds=["paper"], document_id=document_id
        )
    outline: list[PaperSectionOut] = []
    for row in sections:
        label = str(row["section"] or "")
        match = _PAGE_SUFFIX.match(label)
        title = match.group("title") if match else label
        page = int(match.group("page")) if match else None
        if outline and outline[-1].title == title:
            continue  # the same section running onto the next page
        outline.append(PaperSectionOut(title=title or "Passage", page=page))
    return PaperDetail(
        **out.model_dump(),
        abstract=extra["abstract"],
        outline=outline,
        conversations=[ChatSessionOut.model_validate(s) for s in sessions],
    )


@router.delete("/papers/{document_id}")
async def delete_paper(
    document_id: UUID,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> PaperDeletedOut:
    """Delete a paper you uploaded (owners: any paper), with your conversations
    about it — except one whose answer is in a binder, shared or versioned,
    which stays (others' conversations stay too, no longer linked)."""
    from app.services.settings import SettingsService

    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        _, extra = await _paper_out(conn, document_id=document_id, user=user)
        if extra["metadata"].get("kind") != PAPER_KIND:
            raise NotFoundError("paper not found")
        if extra["metadata"].get("uploaded_by") != str(user.user_id) and user.role != "owner":
            raise PermissionDeniedError(
                "only the person who uploaded a paper, or an owner, can delete it"
            )
        sessions = await ChatRepository().list_sessions(
            conn, user_id=user.user_id, kinds=["paper"], document_id=document_id, limit=100
        )
    deleted = kept = 0
    settings_service = SettingsService(pool)
    for session in sessions:
        result = await settings_service.delete_conversations(user, session_id=session["id"])
        deleted += result.deleted
        kept += result.kept
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        await conn.execute(
            "DELETE FROM public.documents WHERE id = $1 AND org_id = $2", document_id, user.org_id
        )
        await TenancyRepository().insert_audit(
            conn,
            org_id=user.org_id,
            user_id=user.user_id,
            action="paper.deleted",
            resource_type="document",
            resource_id=document_id,
            payload={"conversations_deleted": deleted, "conversations_kept": kept},
        )
    return PaperDeletedOut(deleted=True, conversations_deleted=deleted, conversations_kept=kept)
