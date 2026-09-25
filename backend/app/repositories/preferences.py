"""user_preferences: one row per person, written on first save (026)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import asyncpg

from app.repositories.base import PgConnection

# The only columns an upsert may name — the SQL below is assembled from this
# list, never from input.
PREFERENCE_COLUMNS: tuple[str, ...] = (
    "audience",
    "sources_open",
    "show_timeline",
    "voice_name",
    "voice_rate",
    "voice_continuous",
    "learn_scope",
    "learn_depth",
    "followed_specialties",
)


class PreferencesRepository:
    async def get(self, conn: PgConnection, *, user_id: UUID) -> asyncpg.Record | None:
        return await conn.fetchrow(
            "SELECT * FROM public.user_preferences WHERE user_id = $1", user_id
        )

    async def upsert(
        self,
        conn: PgConnection,
        *,
        user_id: UUID,
        org_id: UUID,
        changes: Mapping[str, Any],
    ) -> asyncpg.Record:
        columns = [c for c in PREFERENCE_COLUMNS if c in changes]
        names = ", ".join(["user_id", "org_id", *columns])
        values = ", ".join(f"${i}" for i in range(1, len(columns) + 3))
        assignments = ", ".join([*(f"{c} = EXCLUDED.{c}" for c in columns), "updated_at = now()"])
        row = await conn.fetchrow(
            f"INSERT INTO public.user_preferences ({names}) VALUES ({values}) "
            f"ON CONFLICT (user_id) DO UPDATE SET {assignments} RETURNING *",
            user_id,
            org_id,
            *(changes[c] for c in columns),
        )
        assert row is not None
        return row
