"""Auth bootstrap: first-login provisioning."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import DbPool, get_asyncpg_pool
from app.core.security import CurrentUser, get_current_user
from app.schemas.tenancy import BootstrapRequest, MeOut
from app.services.tenancy import TenancyService

router = APIRouter(prefix="/auth", tags=["auth"])


def get_tenancy_service(
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
) -> TenancyService:
    return TenancyService(pool)


@router.post("/bootstrap")
async def bootstrap(
    body: BootstrapRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[TenancyService, Depends(get_tenancy_service)],
) -> MeOut:
    """Create a profile on first login: fresh org, or join via invite token.

    Idempotent — an already-bootstrapped user gets their current membership.
    """
    return await service.bootstrap(user, org_name=body.org_name, invite_token=body.invite_token)
