"""The weekly evidence digest: a user's own opt-in (Phase 13)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.deps import DbPool, get_asyncpg_pool
from app.core.ratelimit import enforce_rate_limit
from app.core.security import CurrentUser
from app.services import digest

router = APIRouter(prefix="/digest", tags=["digest"])


class DigestPreferencesOut(BaseModel):
    enabled: bool  # the weekly in-app digest
    email: bool  # and an email copy (only meaningful when enabled)
    last_sent_at: datetime | None


class DigestPreferencesUpdate(BaseModel):
    enabled: bool | None = None
    email: bool | None = None


def _out(prefs: digest.DigestPreferences) -> DigestPreferencesOut:
    return DigestPreferencesOut(
        enabled=prefs.enabled, email=prefs.email, last_sent_at=prefs.last_sent_at
    )


@router.get("/preferences")
async def get_digest_preferences(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> DigestPreferencesOut:
    """Whether this user receives the weekly digest, and by which channels."""
    assert user.org_id is not None
    return _out(await digest.get_preferences(pool, org_id=user.org_id, user_id=user.user_id))


@router.patch("/preferences")
async def update_digest_preferences(
    body: DigestPreferencesUpdate,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> DigestPreferencesOut:
    """Opt in or out of the weekly digest (in-app), and of its email copy."""
    assert user.org_id is not None
    return _out(
        await digest.set_preferences(
            pool,
            org_id=user.org_id,
            user_id=user.user_id,
            enabled=body.enabled,
            email=body.email,
        )
    )
