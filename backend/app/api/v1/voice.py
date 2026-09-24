"""Voice endpoints: the session WebSocket plus the REST surface around it.

``/api/v1/voice/ws`` authenticates on the first message (browsers cannot set
headers on a WebSocket upgrade and a token in the URL would land in logs),
binds the session to the caller's tenant, and then multiplexes audio and
control messages for the life of the connection. A dropped socket does not
end the session: ``resume`` with the session id + resume token re-attaches
within ``RESUME_GRACE_SECONDS``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from app.core.deps import DbPool, get_asyncpg_pool, get_redis_client
from app.core.errors import (
    InvalidRequestError,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
)
from app.core.ratelimit import RateLimiter, enforce_rate_limit
from app.core.security import CurrentUser, JWTVerifier, get_current_org
from app.repositories.base import tenant_connection
from app.repositories.voice import VoiceRepository
from app.schemas.voice import (
    TtsQuality,
    VoiceAnalyticsOut,
    VoiceConfigOut,
    VoiceSettingsIn,
    VoiceSettingsOut,
    VoiceTurnList,
    VoiceTurnOut,
)
from app.services.voice_analytics import aggregate_voice
from app.voice import protocol
from app.voice.audio import write_wav
from app.voice.availability import provider_unavailable_reason
from app.voice.registry import SessionRegistry, get_registry
from app.voice.runtime import BOOST_TERMS, VoiceRuntime, get_voice_runtime
from app.voice.session import VoiceSession

logger = structlog.stdlib.get_logger("app.api.voice")

router = APIRouter(prefix="/voice", tags=["voice"])

_AUTH_TIMEOUT_S = 8.0


def _app_state(request: Request) -> Any:
    return request.app.state


def _quality(value: str) -> TtsQuality:
    return "multilingual" if value == "multilingual" else "flash"


class _SocketTransport:
    def __init__(self, socket: WebSocket) -> None:
        self._socket = socket

    async def send_json(self, payload: dict[str, Any]) -> None:
        await self._socket.send_text(json.dumps(payload))

    async def send_bytes(self, data: bytes) -> None:
        await self._socket.send_bytes(data)


async def _resolve_user(socket: WebSocket, token: str) -> CurrentUser:
    """The same identity resolution the REST layer uses, driven by the token
    carried in the ``start``/``resume`` message."""
    verifier = socket.app.state.jwt_verifier
    assert isinstance(verifier, JWTVerifier)
    auth = await verifier.verify(token)
    pool = socket.app.state.db_pool
    profile = await pool.fetchrow(
        "SELECT p.org_id, p.role, p.full_name, p.specialty, o.plan "
        "FROM public.profiles p LEFT JOIN public.organizations o ON o.id = p.org_id "
        "WHERE p.id = $1",
        auth.user_id,
    )
    return CurrentUser(
        user_id=auth.user_id,
        email=auth.email,
        full_name=profile["full_name"] if profile else auth.full_name,
        org_id=profile["org_id"] if profile else None,
        role=profile["role"] if profile else None,
        specialty=profile["specialty"] if profile else None,
        plan=profile["plan"] if profile and profile["plan"] else "free",
    )


@router.websocket("/ws")
async def voice_ws(socket: WebSocket) -> None:
    await socket.accept()
    transport = _SocketTransport(socket)
    runtime: VoiceRuntime = get_voice_runtime(socket.app.state)
    registry: SessionRegistry = get_registry(socket.app.state)
    session: VoiceSession | None = None
    try:
        # 1. First message must be start/resume with a token.
        try:
            raw = await asyncio.wait_for(socket.receive(), timeout=_AUTH_TIMEOUT_S)
        except TimeoutError:
            await transport.send_json(
                protocol.ErrorEvent(code="auth_timeout", message="no start message").model_dump()
            )
            await socket.close(code=4401)
            return
        text = raw.get("text")
        if raw.get("type") == "websocket.disconnect" or text is None:
            await socket.close(code=4400)
            return
        try:
            first = protocol.parse_client_message(text)
        except ValidationError:
            await transport.send_json(
                protocol.ErrorEvent(
                    code="bad_message", message="expected start/resume"
                ).model_dump()
            )
            await socket.close(code=4400)
            return
        if not isinstance(first, protocol.StartMessage | protocol.ResumeMessage):
            await socket.close(code=4400)
            return
        try:
            user = await _resolve_user(socket, first.token)
        except Exception as exc:
            logger.info("voice_auth_failed", error=type(exc).__name__)
            await transport.send_json(
                protocol.ErrorEvent(code="unauthorized", message="invalid token").model_dump()
            )
            await socket.close(code=4401)
            return
        if user.org_id is None or user.role == "viewer":
            await transport.send_json(
                protocol.ErrorEvent(
                    code="forbidden", message="this principal cannot run queries"
                ).model_dump()
            )
            await socket.close(code=4403)
            return

        if isinstance(first, protocol.ResumeMessage):
            session = registry.resume(first.session_id, first.resume_token, user.org_id)
            if session is None:
                await transport.send_json(
                    protocol.ErrorEvent(
                        code="resume_failed", message="session expired or unknown"
                    ).model_dump()
                )
                await socket.close(code=4404)
                return
            await session.attach(transport)
        else:
            # Refuse in words rather than open a session whose providers
            # cannot start — that crashed on the free deployment, and the
            # browser was left recording into nothing.
            reason = runtime.unavailable_reason()
            if reason is not None:
                await transport.send_json(
                    protocol.ErrorEvent(code="voice_unavailable", message=reason).model_dump()
                )
                await socket.close(code=4503)
                return
            pool = socket.app.state.db_pool
            async with tenant_connection(pool, user.org_id, user.user_id) as conn:
                quality = await VoiceRepository().get_tts_quality(conn, org_id=user.org_id)
            session = VoiceSession(
                runtime=runtime,
                pool=pool,
                redis=socket.app.state.redis,
                user=user,
                query_session_id=UUID(first.query_session_id) if first.query_session_id else None,
                transport=transport,
                tts_quality=quality,
            )
            registry.add(session)
            await session.start()

        # 2. Steady state: binary = audio, text = control.
        while True:
            message = await socket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is not None:
                await session.on_audio(data)
                continue
            text = message.get("text")
            if text is None:
                continue
            try:
                parsed = protocol.parse_client_message(text)
            except ValidationError as exc:
                await transport.send_json(
                    protocol.ErrorEvent(code="bad_message", message=str(exc)[:200]).model_dump()
                )
                continue
            if isinstance(parsed, protocol.PingMessage):
                await transport.send_json(protocol.PongEvent().model_dump())
            elif isinstance(parsed, protocol.TextMessage):
                await session.on_text(parsed.text)
            elif isinstance(parsed, protocol.ConfirmMessage):
                await session.on_confirm(parsed.choice)
            elif isinstance(parsed, protocol.ContinueMessage):
                await session.on_continue()
            elif isinstance(parsed, protocol.BargeInMessage):
                await session.barge_in(stop_latency_ms=parsed.stop_latency_ms, source="client")
            elif isinstance(parsed, protocol.PlaybackMessage):
                await session.on_playback(parsed)
            elif isinstance(parsed, protocol.StopMessage):
                await session.on_stop()
                await registry.remove(session)
                session = None
                break
            # start/resume mid-stream are ignored.
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("voice_ws_failed")
    finally:
        if session is not None:
            # Keep the session alive for a resume; the registry reaps it later.
            session.detach()
            registry.park(session)
        with contextlib.suppress(Exception):
            await socket.close()


# -- REST -------------------------------------------------------------------------------


_TRANSCRIBE_MAX_BYTES = 5 * 1024 * 1024
_TRANSCRIBE_PER_MINUTE = 20
_SPEAK_PER_MINUTE = 90
_SPEAK_MAX_CHARS = 1200
_AUDIO_TYPES = ("audio/webm", "audio/ogg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/mpeg")


async def _voice_rate_limit(redis: Any, user: CurrentUser, kind: str, per_minute: int) -> None:
    """Voice has its own per-person bucket: one spoken turn is a
    transcription and several audio clips, which would otherwise eat the
    plan's request limit that chat and search share."""
    ok, retry_after = await RateLimiter(redis).hit(f"rl:{kind}:{user.user_id}", per_minute)
    if not ok:
        raise RateLimitError(f"{kind} limit of {per_minute}/min reached", retry_after=retry_after)


@router.post("/transcribe")
async def transcribe(
    request: Request,
    request_user: Annotated[CurrentUser, Depends(get_current_org)],
    socket_app: Annotated[Any, Depends(_app_state)],
    redis: Annotated[Any, Depends(get_redis_client)],
) -> dict[str, str]:
    """A spoken question from the chat → its text: voice typing, and each
    turn of a voice conversation. Deepgram's medical model on the deployed
    server, faster-whisper locally; a server with neither says so."""
    await _voice_rate_limit(redis, request_user, "transcribe", _TRANSCRIBE_PER_MINUTE)
    runtime = get_voice_runtime(socket_app)
    stt = runtime.stt
    if not hasattr(stt, "transcribe") or runtime.unavailable_reason() is not None:
        raise ServiceUnavailableError(
            runtime.unavailable_reason() or "Voice typing needs the cloud speech service."
        )
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in _AUDIO_TYPES:
        raise InvalidRequestError(f"unsupported audio type: {content_type or 'none'}")
    audio = await request.body()
    if not audio:
        raise InvalidRequestError("no audio received")
    if len(audio) > _TRANSCRIBE_MAX_BYTES:
        raise InvalidRequestError("the recording is too long — keep it under a minute")
    try:
        text = await stt.transcribe(audio, request.headers.get("content-type", content_type))
    except Exception as exc:
        logger.warning("transcribe_failed", error=f"{type(exc).__name__}: {exc}")
        raise ServiceUnavailableError(
            "The speech service could not transcribe that — try again."
        ) from exc
    return {"text": text}


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=_SPEAK_MAX_CHARS)


@router.post("/speak")
async def speak(
    body: SpeakRequest,
    request_user: Annotated[CurrentUser, Depends(get_current_org)],
    socket_app: Annotated[Any, Depends(_app_state)],
    redis: Annotated[Any, Depends(get_redis_client)],
) -> Response:
    """Text → speech, as a WAV clip the browser plays: the chat reads its
    answers aloud a few sentences at a time with this. Deepgram Aura-2 on
    the deployed server, the operating system's voice locally."""
    await _voice_rate_limit(redis, request_user, "speak", _SPEAK_PER_MINUTE)
    runtime = get_voice_runtime(socket_app)
    reason = provider_unavailable_reason(runtime.tts)
    if reason is not None:
        raise ServiceUnavailableError(reason)
    stream = await runtime.tts.open(quality="flash", lexicon_pls=None)
    try:
        pcm = b"".join([chunk async for chunk in stream.synthesize(body.text)])
    finally:
        await stream.close()
    if not pcm:
        raise ServiceUnavailableError("The speech service returned no audio — try again.")
    return Response(content=write_wav(pcm), media_type="audio/wav")


@router.get("/config")
async def voice_config(
    request_user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    socket_app: Annotated[Any, Depends(_app_state)],
) -> VoiceConfigOut:
    """What the client is talking to: backend, models, vocabulary sizes."""
    assert request_user.org_id is not None
    runtime = get_voice_runtime(socket_app)
    vocabulary = await runtime.vocabulary(pool)
    lexicon = runtime.lexicon_for(vocabulary)
    async with tenant_connection(pool, request_user.org_id, request_user.user_id) as conn:
        quality = await VoiceRepository().get_tts_quality(conn, org_id=request_user.org_id)
    return VoiceConfigOut(
        backend=runtime.settings.voice_backend,
        stt_provider=runtime.stt.name,
        stt_model=runtime.stt.model,
        tts_provider=runtime.tts.name,
        tts_model=getattr(runtime.tts, "model", None)
        or (
            runtime.settings.voice_tts_model
            if runtime.settings.voice_backend == "cloud"
            else f"local:{runtime.settings.voice_offline_voice}"
        ),
        tts_quality=_quality(quality),
        boost_terms=min(len(vocabulary.terms), BOOST_TERMS),
        lexicon_entries=len(lexicon.entries),
        lexicon_coverage=round(lexicon.coverage, 3),
        lasa_pairs=len(runtime.lasa),
        available=runtime.unavailable_reason() is None,
        unavailable_reason=runtime.unavailable_reason(),
    )


@router.get("/settings")
async def get_settings_(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> VoiceSettingsOut:
    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        quality = await VoiceRepository().get_tts_quality(conn, org_id=user.org_id)
    return VoiceSettingsOut(tts_quality=_quality(quality))


@router.patch("/settings")
async def set_settings(
    body: VoiceSettingsIn,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> VoiceSettingsOut:
    """Flash (latency) vs Multilingual (prosody): an owner-level org setting."""
    assert user.org_id is not None
    if user.role != "owner":
        raise PermissionDeniedError("only an owner can change voice settings")
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        await VoiceRepository().set_tts_quality(conn, org_id=user.org_id, quality=body.tts_quality)
    return VoiceSettingsOut(tts_quality=body.tts_quality)


@router.get("/turns")
async def list_turns(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    query_session_id: UUID | None = None,
    voice_session_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> VoiceTurnList:
    """Per-turn waterfalls (the session view's debug surface)."""
    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        rows, total = await VoiceRepository().list_turns(
            conn,
            org_id=user.org_id,
            query_session_id=query_session_id,
            voice_session_id=voice_session_id,
            limit=limit,
            offset=offset,
        )
    return VoiceTurnList(items=[VoiceTurnOut.model_validate(r) for r in rows], total=total)


@router.get("/analytics")
async def voice_analytics(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> VoiceAnalyticsOut:
    """Aggregate latency per leg, speculation economics, confirmations,
    barge-ins, masks, and the cost of interactivity (11G.5, 11D.4)."""
    assert user.org_id is not None
    async with tenant_connection(pool, user.org_id, user.user_id) as conn:
        rows = await VoiceRepository().aggregate(conn, org_id=user.org_id, days=days)
    return aggregate_voice(rows, days=days)
