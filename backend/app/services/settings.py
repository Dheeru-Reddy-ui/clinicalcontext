"""Settings: a person's profile and preferences, and what they can do with
their own data — take a copy of it, or delete their conversations.

Everything a person changes about themselves runs under their own tenant
context, so RLS and the column grants of 026 apply: the profile update can
touch only the name and specialty, whatever this code says. Deleting
conversations is the exception — sessions, questions and answers have no
DELETE policy for clients at all — so it runs in the service context with
the owner and organization pinned in every predicate, as role changes do.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from app.core.deps import DbPool
from app.core.errors import InvalidRequestError, NotFoundError
from app.core.security import CurrentUser
from app.knowledge.specialties import BY_SLUG
from app.repositories.base import tenant_connection
from app.repositories.chat import ChatRepository
from app.repositories.preferences import PreferencesRepository
from app.repositories.tenancy import TenancyRepository
from app.schemas.settings import (
    DELETABLE_KINDS,
    ConversationsDeletedOut,
    PreferencesOut,
    PreferencesUpdate,
    ProfileUpdate,
)
from app.schemas.tenancy import MeOut
from app.services import digest
from app.services.tenancy import TenancyService
from app.voice.voices import find_voice

EXPORT_FORMAT = "clinicalcontext-export/1"
_EXPORT_MAX_CONVERSATIONS = 1000
# What a source is in an export: enough to find it again, not the passage.
_SOURCE_KEYS = ("marker", "title", "journal", "pmid", "doi", "url")


def _preferences_out(row: asyncpg.Record | None) -> PreferencesOut:
    if row is None:
        return PreferencesOut()
    rate = row["voice_rate"]
    return PreferencesOut(
        audience=row["audience"],
        sources_open=row["sources_open"],
        show_timeline=row["show_timeline"],
        voice_name=row["voice_name"],
        voice_rate=float(rate) if isinstance(rate, Decimal) else rate,
        voice_continuous=row["voice_continuous"],
        learn_scope=row["learn_scope"],
        learn_depth=row["learn_depth"],
        followed_specialties=list(row["followed_specialties"]),
        saved=True,
        updated_at=row["updated_at"],
    )


def _clean_specialties(slugs: list[str]) -> list[str]:
    unknown = [s for s in slugs if s not in BY_SLUG]
    if unknown:
        raise InvalidRequestError(f"unknown specialty: {', '.join(sorted(set(unknown)))}")
    return list(dict.fromkeys(slugs))  # de-duplicated, first choice first


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


class SettingsService:
    def __init__(self, pool: DbPool) -> None:
        self._pool = pool
        self._prefs = PreferencesRepository()

    # -- preferences -------------------------------------------------------------------

    async def preferences(self, user: CurrentUser) -> PreferencesOut:
        assert user.org_id is not None
        async with tenant_connection(self._pool, user.org_id, user.user_id) as conn:
            return _preferences_out(await self._prefs.get(conn, user_id=user.user_id))

    async def update_preferences(
        self, user: CurrentUser, body: PreferencesUpdate
    ) -> PreferencesOut:
        assert user.org_id is not None
        changes: dict[str, Any] = body.model_dump(exclude_unset=True, exclude_none=True)
        if "voice_name" in changes and find_voice(changes["voice_name"]) is None:
            raise InvalidRequestError(f"unknown voice: {changes['voice_name']}")
        if "voice_rate" in changes:
            changes["voice_rate"] = round(float(changes["voice_rate"]), 2)
        if "followed_specialties" in changes:
            changes["followed_specialties"] = _clean_specialties(changes["followed_specialties"])
        async with tenant_connection(self._pool, user.org_id, user.user_id) as conn:
            if not changes:
                return _preferences_out(await self._prefs.get(conn, user_id=user.user_id))
            row = await self._prefs.upsert(
                conn, user_id=user.user_id, org_id=user.org_id, changes=changes
            )
        return _preferences_out(row)

    # -- profile -----------------------------------------------------------------------------

    async def update_profile(self, user: CurrentUser, body: ProfileUpdate) -> MeOut:
        assert user.org_id is not None
        fields = body.model_dump(exclude_unset=True)
        # An empty field clears it; the name shown then falls back to the email.
        values = {k: (v or None) for k, v in fields.items()}
        if values:
            assignments = ", ".join(f"{k} = ${i}" for i, k in enumerate(values, start=2))
            async with tenant_connection(self._pool, user.org_id, user.user_id) as conn:
                # Runs as the person under RLS; 026 grants UPDATE on these two
                # columns only, so this statement could not set a role even if
                # it tried.
                status = await conn.execute(
                    f"UPDATE public.profiles SET {assignments} WHERE id = $1",
                    user.user_id,
                    *values.values(),
                )
                if not status.endswith(" 1"):
                    raise NotFoundError("profile not found")
                await TenancyRepository().insert_audit(
                    conn,
                    org_id=user.org_id,
                    user_id=user.user_id,
                    action="profile.updated",
                    resource_type="profile",
                    resource_id=user.user_id,
                    payload={"fields": sorted(values)},
                )
        updated = user.model_copy(
            update={
                "full_name": values.get("full_name", user.full_name),
                "specialty": values.get("specialty", user.specialty),
            }
        )
        return await TenancyService(self._pool).get_me(updated)

    # -- your data -----------------------------------------------------------------------

    async def export(self, user: CurrentUser) -> dict[str, Any]:
        """A copy of everything this person put into ClinicalContext, as JSON."""
        assert user.org_id is not None
        me = await TenancyService(self._pool).get_me(user)
        digest_prefs = await digest.get_preferences(
            self._pool, org_id=user.org_id, user_id=user.user_id
        )
        repo = ChatRepository()
        async with tenant_connection(self._pool, user.org_id, user.user_id) as conn:
            preferences = _preferences_out(await self._prefs.get(conn, user_id=user.user_id))
            sessions = await repo.list_sessions(
                conn,
                user_id=user.user_id,
                kinds=["ask", *DELETABLE_KINDS],
                limit=_EXPORT_MAX_CONVERSATIONS,
            )
            conversations = []
            for session in sessions:
                detail = await repo.session_messages(
                    conn, session_id=session["id"], user_id=user.user_id
                )
                if detail is None:
                    continue
                conversations.append(
                    {
                        "id": detail["id"],
                        "kind": detail["kind"],
                        "title": detail["title"],
                        "created_at": detail["created_at"],
                        "turns": [
                            {
                                "asked_at": turn["created_at"],
                                "question": turn["question"],
                                "written_for": turn["audience"],
                                "status": turn["status"],
                                "answer": turn["answer"],
                                "sources": [
                                    {key: citation.get(key) for key in _SOURCE_KEYS}
                                    for citation in turn["citations"]
                                    if isinstance(citation, dict)
                                ],
                            }
                            for turn in detail["turns"]
                        ],
                    }
                )
            followed = await conn.fetch(
                "SELECT answer_id, created_at FROM public.followed_answers "
                "WHERE user_id = $1 ORDER BY created_at",
                user.user_id,
            )
            feedback = await conn.fetch(
                "SELECT answer_id, rating::text AS rating, reason::text AS reason, comment, "
                "created_at FROM public.feedback WHERE user_id = $1 ORDER BY created_at",
                user.user_id,
            )
            practice = await conn.fetch(
                "SELECT mode, level, specialty, topic, question, chosen, answer, correct, "
                "created_at FROM public.tutor_attempts WHERE user_id = $1 ORDER BY created_at",
                user.user_id,
            )
        exported = _jsonable(
            {
                "format": EXPORT_FORMAT,
                "exported_at": datetime.now(UTC),
                "profile": {
                    "user_id": me.user_id,
                    "email": me.email,
                    "full_name": me.full_name,
                    "specialty": me.specialty,
                    "role": me.role,
                },
                "organization": {"name": me.org.name, "plan": me.org.plan},
                "preferences": preferences.model_dump(),
                "weekly_digest": {"in_app": digest_prefs.enabled, "email": digest_prefs.email},
                "conversations": conversations,
                "followed_answers": [dict(r) for r in followed],
                "feedback": [dict(r) for r in feedback],
                "tutor_practice": [dict(r) for r in practice],
            }
        )
        assert isinstance(exported, dict)
        return exported

    async def delete_conversations(
        self, user: CurrentUser, *, session_id: UUID | None = None
    ) -> ConversationsDeletedOut:
        """Delete this person's assistant conversations — one, or all of them.

        A conversation stays when deleting it would reach into something
        other people rely on: an answer in it saved to a binder, behind a
        public link, or with recorded versions."""
        assert user.org_id is not None
        async with self._pool.acquire() as conn, conn.transaction():
            targets = await conn.fetch(
                """
                SELECT s.id,
                       EXISTS (
                           SELECT 1 FROM public.queries q
                           JOIN public.answers a ON a.query_id = q.id
                           WHERE q.session_id = s.id AND (
                               EXISTS (SELECT 1 FROM public.binder_items b
                                       WHERE b.answer_id = a.id)
                               OR EXISTS (SELECT 1 FROM public.share_links l
                                          WHERE l.answer_id = a.id)
                               OR EXISTS (SELECT 1 FROM public.answer_versions v
                                          WHERE v.answer_id = a.id)
                           )
                       ) AS in_use
                FROM public.query_sessions s
                WHERE s.user_id = $1 AND s.org_id = $2 AND s.kind = ANY($3::text[])
                  AND ($4::uuid IS NULL OR s.id = $4)
                """,
                user.user_id,
                user.org_id,
                list(DELETABLE_KINDS),
                session_id,
            )
            if session_id is not None and not targets:
                raise NotFoundError("conversation not found")
            doomed = [t["id"] for t in targets if not t["in_use"]]
            if doomed:
                await conn.execute(
                    "DELETE FROM public.query_sessions WHERE id = ANY($1::uuid[]) "
                    "AND user_id = $2 AND org_id = $3",
                    doomed,
                    user.user_id,
                    user.org_id,
                )
                await TenancyRepository().insert_audit(
                    conn,
                    org_id=user.org_id,
                    user_id=user.user_id,
                    action="conversations.deleted",
                    resource_type="query_session",
                    resource_id=session_id,
                    payload={"deleted": len(doomed), "kept": len(targets) - len(doomed)},
                )
        return ConversationsDeletedOut(deleted=len(doomed), kept=len(targets) - len(doomed))


def export_filename(now: datetime | None = None) -> str:
    return f"clinicalcontext-export-{(now or datetime.now(UTC)).date().isoformat()}.json"


def dump_export(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
