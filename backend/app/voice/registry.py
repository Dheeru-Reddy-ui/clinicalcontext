"""In-process registry of live voice sessions (11A.5 reconnection).

A session outlives its socket: when the client drops, the session is parked
and can be resumed with its id + resume token for ``RESUME_GRACE_SECONDS``.
Sessions are bound to the tenant that created them — a resume from another
org is refused. A parked session that nobody resumes is closed by the reaper.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog

from app.voice.session import VoiceSession

logger = structlog.stdlib.get_logger("app.voice.registry")

RESUME_GRACE_SECONDS = 120.0


@dataclass(slots=True)
class _Entry:
    session: VoiceSession
    parked_at: float | None = None


class SessionRegistry:
    def __init__(self, *, grace_seconds: float = RESUME_GRACE_SECONDS) -> None:
        self._entries: dict[str, _Entry] = {}
        self._grace = grace_seconds
        self._reaper: asyncio.Task[None] | None = None

    def add(self, session: VoiceSession) -> None:
        self._entries[str(session.session_id)] = _Entry(session)
        self._ensure_reaper()

    def resume(self, session_id: str, resume_token: str, org_id: UUID) -> VoiceSession | None:
        entry = self._entries.get(session_id)
        if entry is None:
            return None
        session = entry.session
        if session.resume_token != resume_token or session.org_id != org_id:
            logger.warning("voice_resume_refused", session_id=session_id)
            return None
        entry.parked_at = None
        return session

    def park(self, session: VoiceSession) -> None:
        entry = self._entries.get(str(session.session_id))
        if entry is not None:
            entry.parked_at = time.monotonic()

    async def remove(self, session: VoiceSession) -> None:
        self._entries.pop(str(session.session_id), None)
        await session.close()

    def __len__(self) -> int:
        return len(self._entries)

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_loop(), name="voice-reaper")

    async def _reap_loop(self) -> None:
        while self._entries:
            await asyncio.sleep(15.0)
            now = time.monotonic()
            expired = [
                key
                for key, entry in self._entries.items()
                if entry.parked_at is not None and now - entry.parked_at > self._grace
            ]
            for key in expired:
                entry = self._entries.pop(key)
                logger.info("voice_session_reaped", session_id=key)
                with contextlib.suppress(Exception):
                    await entry.session.close()

    async def close_all(self) -> None:
        for entry in list(self._entries.values()):
            with contextlib.suppress(Exception):
                await entry.session.close()
        self._entries.clear()
        if self._reaper is not None:
            self._reaper.cancel()


def get_registry(app_state: Any) -> SessionRegistry:
    registry = getattr(app_state, "voice_registry", None)
    if not isinstance(registry, SessionRegistry):
        registry = SessionRegistry()
        app_state.voice_registry = registry
    return registry
