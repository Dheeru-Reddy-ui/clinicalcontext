"""The chat assistant, the Learn tab and the Treatment tab.

- ``POST /chat`` streams one assistant turn as server-sent events;
  ``/chat/sessions`` lists and opens conversations.
- ``/learn/specialties`` is the map of MBBS and PG subjects; each one's
  ``/latest`` is PubMed's newest high-evidence papers.
- ``/treatment/step`` runs the symptom check one question at a time.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.assistant.chat import ChatAssistant
from app.core.deps import DbPool, get_asyncpg_pool, get_redis_client
from app.core.errors import InvalidRequestError, NotFoundError, PermissionDeniedError
from app.core.ratelimit import enforce_rate_limit
from app.core.security import CurrentUser
from app.knowledge.feed import latest_research
from app.knowledge.specialties import LEVEL_LABELS, SPECIALTIES, Specialty, get_specialty
from app.llm.chat import configured_providers
from app.repositories.base import tenant_connection
from app.repositories.chat import ChatRepository
from app.schemas.assistant import (
    AssessmentOut,
    AssistantStatus,
    ChatRequest,
    ChatSessionDetail,
    ChatSessionOut,
    ComplaintOut,
    DoctorOptionOut,
    FeedItemOut,
    MedicineOut,
    OptionOut,
    QuestionOut,
    ReasonOut,
    RenameSession,
    SourceOut,
    SpecialtyFeedOut,
    SpecialtyOut,
    TreatmentStepOut,
    TreatmentStepRequest,
)
from app.treatment.engine import ACTIONS, HEADLINES, UnknownComplaint, step
from app.treatment.formulary import Profile
from app.treatment.model import Assessment, Question
from app.treatment.protocols import PROTOCOLS
from app.treatment.sources import SOURCES

logger = structlog.stdlib.get_logger("app.api.assistant")

router = APIRouter(tags=["assistant"])


def sse(event: dict[str, object]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


def stream_events(events: AsyncIterator[dict[str, Any]]) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        try:
            async for event in events:
                yield sse(event)
        except Exception as exc:  # never leak a stack trace into the stream
            logger.exception("chat_stream_failed")
            yield sse(
                {
                    "stage": "error",
                    "message": "Something went wrong while answering. Please try again.",
                    "data": {"type": type(exc).__name__},
                }
            )

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# -- chat ---------------------------------------------------------------------------------


def assistant_status() -> AssistantStatus:
    providers = configured_providers()
    names = list(dict.fromkeys(p.name for p in providers))
    if names:
        message = "Answers are written by a language model from the retrieved sources."
    else:
        message = (
            "No language model is configured, so answers are quoted straight from the "
            "sources. Add a free Groq key (docs/DEPLOY.md, Part 5) for conversational answers."
        )
    return AssistantStatus(llm_available=bool(names), providers=names, message=message)


@router.get("/assistant/status")
async def get_assistant_status(
    _user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
) -> AssistantStatus:
    return assistant_status()


@router.post("/chat")
async def chat(
    body: ChatRequest,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    redis: Annotated[Any, Depends(get_redis_client)],
) -> StreamingResponse:
    if user.role == "viewer":
        raise PermissionDeniedError("this principal has read-only access and cannot chat")
    assert user.org_id is not None
    if body.specialty is not None and get_specialty(body.specialty) is None:
        raise InvalidRequestError(f"unknown specialty: {body.specialty}")
    assistant = ChatAssistant(pool, redis)
    return stream_events(
        assistant.reply(
            message=body.message,
            audience=body.audience,
            org_id=user.org_id,
            user_id=user.user_id,
            session_id=body.session_id,
            kind=body.kind,
            specialty=body.specialty,
            level=body.level,
        )
    )


@router.get("/chat/sessions")
async def list_chat_sessions(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    kind: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ChatSessionOut]:
    assert user.org_id is not None
    kinds = [k for k in (kind or ["chat"]) if k in ("chat", "learn", "treatment", "ask", "voice")]
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        rows = await ChatRepository().list_sessions(
            conn, user_id=user.user_id, kinds=kinds or ["chat"], limit=limit
        )
    return [ChatSessionOut.model_validate(r) for r in rows]


@router.get("/chat/sessions/{session_id}")
async def get_chat_session(
    session_id: UUID,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> ChatSessionDetail:
    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        detail = await ChatRepository().session_messages(
            conn, session_id=session_id, user_id=user.user_id
        )
    if detail is None:
        raise NotFoundError("conversation not found")
    return ChatSessionDetail.model_validate(detail)


@router.patch("/chat/sessions/{session_id}")
async def rename_chat_session(
    session_id: UUID,
    body: RenameSession,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> dict[str, bool]:
    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        renamed = await ChatRepository().rename_session(
            conn, session_id=session_id, user_id=user.user_id, title=body.title.strip()
        )
    if not renamed:
        raise NotFoundError("conversation not found")
    return {"renamed": True}


# -- learn ----------------------------------------------------------------------------------


def specialty_out(specialty: Specialty) -> SpecialtyOut:
    return SpecialtyOut(
        slug=specialty.slug,
        name=specialty.name,
        level=specialty.level,
        level_label=LEVEL_LABELS[specialty.level],
        topics=list(specialty.topics),
    )


@router.get("/learn/specialties")
async def list_specialties(
    _user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
) -> list[SpecialtyOut]:
    return [specialty_out(s) for s in SPECIALTIES]


@router.get("/learn/specialties/{slug}/latest")
async def specialty_latest(
    slug: str,
    _user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    redis: Annotated[Any, Depends(get_redis_client)],
    days: Annotated[int, Query(ge=30, le=730)] = 180,
) -> SpecialtyFeedOut:
    specialty = get_specialty(slug)
    if specialty is None:
        raise NotFoundError(f"unknown specialty: {slug}")
    try:
        items = await latest_research(specialty, redis=redis, days=days)
    except Exception as exc:
        logger.warning("research_feed_failed", specialty=slug, error=f"{type(exc).__name__}")
        return SpecialtyFeedOut(
            specialty=specialty_out(specialty),
            items=[],
            days=days,
            available=False,
            message="PubMed could not be reached just now — try again in a minute.",
        )
    return SpecialtyFeedOut(
        specialty=specialty_out(specialty),
        items=[FeedItemOut(**asdict(i)) for i in items],
        days=days,
    )


# -- treatment ------------------------------------------------------------------------------


def _complaint(complaint_id: str) -> ComplaintOut:
    protocol = PROTOCOLS[complaint_id]
    return ComplaintOut(id=protocol.id, name=protocol.name, summary=protocol.summary)


def _question(question: Question, profile: Profile) -> QuestionOut:
    return QuestionOut(
        id=question.id,
        text=question.text,
        kind=question.kind,
        help=question.help,
        options=[OptionOut(id=o.id, label=o.label) for o in question.options_for(profile)],
    )


def assessment_out(assessment: Assessment) -> AssessmentOut:
    return AssessmentOut(
        urgency=assessment.urgency,
        headline=HEADLINES[assessment.urgency],
        action=ACTIONS[assessment.urgency],
        reasons=[
            ReasonOut(text=r.text, urgency=r.urgency, source=r.source) for r in assessment.reasons
        ],
        possible_causes=assessment.possible_causes,
        self_care=assessment.self_care,
        medicines=[
            MedicineOut(
                key=m.key,
                name=m.name,
                purpose=m.purpose,
                suitable=m.suitable,
                dose=m.dose,
                how_often=m.how_often,
                maximum=m.maximum,
                notes=m.notes,
                reason_not_suitable=m.reason_not_suitable,
                sources=m.source_keys,
            )
            for m in assessment.medicines
        ],
        doctor_may=[DoctorOptionOut(text=d.text, source=d.source) for d in assessment.doctor_may],
        tests=assessment.tests,
        see_doctor_if=assessment.see_doctor_if,
        sources=[
            SourceOut(key=s.key, title=s.title, publisher=s.publisher, url=s.url)
            for s in (SOURCES[k] for k in assessment.source_keys if k in SOURCES)
        ],
    )


@router.get("/treatment/complaints")
async def list_complaints(
    _user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
) -> list[ComplaintOut]:
    return [_complaint(key) for key in PROTOCOLS]


def run_step(body: TreatmentStepRequest) -> TreatmentStepOut:
    try:
        result = step(body.complaint, body.profile, body.answers)
    except UnknownComplaint as exc:
        raise NotFoundError(f"unknown complaint: {body.complaint}") from exc
    return TreatmentStepOut(
        complaint=_complaint(body.complaint),
        question=_question(result.question, body.profile) if result.question else None,
        assessment=assessment_out(result.assessment) if result.assessment else None,
        answered=result.answered,
        total=result.total,
    )


@router.post("/treatment/step")
async def treatment_step(
    body: TreatmentStepRequest,
    _user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
) -> TreatmentStepOut:
    """Stateless: nothing about the person is stored."""
    return run_step(body)
