"""Public answer permalinks.

Two halves with different trust:

- **Managing links** runs under the caller's tenant context (RLS); a clinician
  can only share answers their org can see.
- **Resolving a slug** is unauthenticated by definition, so it runs in the
  service context — and therefore enforces every gate itself, explicitly:
  the link exists, the link is enabled, and the org has not switched public
  sharing off. Any failure is a plain 404: a disabled or unknown slug must be
  indistinguishable from one that never existed.
"""

from __future__ import annotations

import json
import secrets
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.errors import NotFoundError
from app.repositories.base import tenant_connection
from app.schemas.sharing import PublicAnswerOut, ShareLinkOut, SupersededInfo

if TYPE_CHECKING:
    import asyncpg

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

_SLUG_BYTES = 9  # 12 url-safe chars: unguessable, still typeable


def _json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    return json.loads(value) if isinstance(value, str) else value


def _link(row: asyncpg.Record) -> ShareLinkOut:
    return ShareLinkOut(
        id=row["id"],
        answer_id=row["answer_id"],
        slug=row["slug"],
        enabled=row["enabled"],
        path=f"/a/{row['slug']}",
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


class SharingService:
    def __init__(self, pool: DbPool) -> None:
        self._pool = pool

    # -- tenant side ---------------------------------------------------------------

    async def create_link(self, *, org_id: UUID, user_id: UUID, answer_id: UUID) -> ShareLinkOut:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            visible = await conn.fetchval("SELECT 1 FROM public.answers WHERE id = $1", answer_id)
            if visible is None:
                raise NotFoundError("answer not found")
            # One live link per answer: re-sharing returns the existing one.
            existing = await conn.fetchrow(
                "SELECT * FROM public.share_links WHERE answer_id = $1 AND enabled ORDER BY "
                "created_at LIMIT 1",
                answer_id,
            )
            if existing is not None:
                return _link(existing)
            row = await conn.fetchrow(
                "INSERT INTO public.share_links (org_id, answer_id, slug, created_by) "
                "VALUES ($1, $2, $3, $4) RETURNING *",
                org_id,
                answer_id,
                secrets.token_urlsafe(_SLUG_BYTES),
                user_id,
            )
        assert row is not None
        return _link(row)

    async def list_links(self, *, org_id: UUID, user_id: UUID) -> list[ShareLinkOut]:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            rows = await conn.fetch(
                "SELECT * FROM public.share_links WHERE org_id = $1 ORDER BY created_at DESC",
                org_id,
            )
        return [_link(r) for r in rows]

    async def revoke_link(self, *, org_id: UUID, user_id: UUID, link_id: UUID) -> None:
        """Disable, never delete: the audit of what was shared must survive."""
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            status = await conn.execute(
                "UPDATE public.share_links SET enabled = false WHERE id = $1 AND enabled",
                link_id,
            )
        if not status.endswith("1"):
            raise NotFoundError("share link not found")

    async def get_policy(self, *, org_id: UUID, user_id: UUID) -> bool:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            value = await conn.fetchval(
                "SELECT public_sharing_enabled FROM public.organizations WHERE id = $1", org_id
            )
        return bool(value)

    async def set_policy(self, *, org_id: UUID, enabled: bool) -> bool:
        """Service context: organizations rows are owner-administered via the
        service role (as role changes are), scoped by the explicit org id."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.organizations SET public_sharing_enabled = $2 WHERE id = $1",
                org_id,
                enabled,
            )
        return enabled

    # -- public side (service context, every gate explicit) --------------------------

    async def resolve(self, slug: str) -> PublicAnswerOut:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT s.slug, s.enabled, o.public_sharing_enabled, o.name AS organization,
                       q.raw_query, a.id AS answer_id, a.content, a.citations,
                       a.confidence::text AS confidence, a.evidence_grade::text AS evidence_grade,
                       a.has_contradiction, a.abstained, a.reasoning, a.model, a.prompt_version,
                       a.created_at
                FROM public.share_links s
                JOIN public.organizations o ON o.id = s.org_id
                JOIN public.answers a ON a.id = s.answer_id
                JOIN public.queries q ON q.id = a.query_id
                WHERE s.slug = $1
                """,
                slug,
            )
            if row is None or not row["enabled"] or not row["public_sharing_enabled"]:
                raise NotFoundError()
            latest = await conn.fetchrow(
                """
                SELECT version, content, citations, confidence::text AS confidence, diff,
                       created_at
                FROM public.answer_versions
                WHERE answer_id = $1 AND version > 1
                ORDER BY version DESC LIMIT 1
                """,
                row["answer_id"],
            )
        superseded = (
            SupersededInfo(
                latest_version=latest["version"],
                superseded_at=latest["created_at"],
                content=latest["content"],
                citations=_json(latest["citations"], []),
                confidence=latest["confidence"],
                diff=_json(latest["diff"], None),
            )
            if latest is not None
            else None
        )
        return PublicAnswerOut(
            slug=row["slug"],
            organization=row["organization"],
            query=row["raw_query"],
            content=row["content"],
            citations=_json(row["citations"], []),
            confidence=row["confidence"],
            evidence_grade=row["evidence_grade"],
            has_contradiction=row["has_contradiction"],
            abstained=row["abstained"],
            reasoning=_json(row["reasoning"], {}),
            model=row["model"],
            prompt_version=row["prompt_version"],
            answered_at=row["created_at"],
            superseded=superseded,
        )
