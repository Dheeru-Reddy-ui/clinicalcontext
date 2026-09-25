"""The signed-in person's own settings (the Settings page).

Profile and preferences are theirs to change; a copy of their data and the
deletion of their assistant conversations are theirs to ask for. Reads that
screens make on every visit (preferences) do not spend the plan's request
budget; writes, exports and deletions do, or have their own bucket.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from app.core.deps import DbPool, get_asyncpg_pool, get_redis_client
from app.core.errors import RateLimitError
from app.core.ratelimit import RateLimiter, enforce_rate_limit
from app.core.security import CurrentUser, get_current_org
from app.schemas.settings import (
    ConversationsDeletedOut,
    PreferencesOut,
    PreferencesUpdate,
    ProfileUpdate,
)
from app.schemas.tenancy import MeOut
from app.services.settings import SettingsService, dump_export, export_filename

router = APIRouter(prefix="/me", tags=["settings"])

_EXPORTS_PER_MINUTE = 3


def get_settings_service(pool: Annotated[DbPool, Depends(get_asyncpg_pool)]) -> SettingsService:
    return SettingsService(pool)


@router.get("/preferences")
async def get_preferences(
    user: Annotated[CurrentUser, Depends(get_current_org)],
    service: Annotated[SettingsService, Depends(get_settings_service)],
) -> PreferencesOut:
    """This person's defaults for the assistant, voice and Learn."""
    return await service.preferences(user)


@router.patch("/preferences")
async def update_preferences(
    body: PreferencesUpdate,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    service: Annotated[SettingsService, Depends(get_settings_service)],
) -> PreferencesOut:
    """Change some preferences; the fields not sent keep their values."""
    return await service.update_preferences(user, body)


@router.patch("/profile")
async def update_profile(
    body: ProfileUpdate,
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    service: Annotated[SettingsService, Depends(get_settings_service)],
) -> MeOut:
    """Change your name or specialty. Role and organization are not yours to set."""
    return await service.update_profile(user, body)


@router.get("/export")
async def export_my_data(
    user: Annotated[CurrentUser, Depends(get_current_org)],
    redis: Annotated[Any, Depends(get_redis_client)],
    service: Annotated[SettingsService, Depends(get_settings_service)],
) -> Response:
    """A JSON copy of your profile, preferences, conversations and feedback."""
    ok, retry_after = await RateLimiter(redis).hit(f"rl:export:{user.user_id}", _EXPORTS_PER_MINUTE)
    if not ok:
        raise RateLimitError(
            f"export limit of {_EXPORTS_PER_MINUTE}/min reached", retry_after=retry_after
        )
    data = await service.export(user)
    return Response(
        content=dump_export(data),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{export_filename()}"',
            "Cache-Control": "no-store",
        },
    )


@router.delete("/conversations")
async def delete_my_conversations(
    user: Annotated[CurrentUser, Depends(enforce_rate_limit)],
    service: Annotated[SettingsService, Depends(get_settings_service)],
) -> ConversationsDeletedOut:
    """Delete all your assistant conversations (Chat, Learn, Treatment, voice).

    Evidence searches stay: they are your organization's record. So does any
    conversation with an answer saved to a binder, shared publicly, or with
    recorded versions; ``kept`` counts them."""
    return await service.delete_conversations(user)
