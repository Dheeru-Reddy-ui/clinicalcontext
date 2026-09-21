"""Organization, membership, and invite routes.

RBAC happens here via dependencies (require_role); tenant scoping happens in
the service/repository layers and, ultimately, in RLS.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.v1.auth import get_tenancy_service
from app.core.security import CurrentUser, get_current_org, get_current_user, require_role
from app.schemas.tenancy import (
    CreateOrgRequest,
    InviteAcceptRequest,
    InviteCreateRequest,
    InviteOut,
    InvitesOut,
    MemberOut,
    MembersOut,
    MeOut,
    RoleUpdateRequest,
)
from app.services.tenancy import TenancyService

router = APIRouter(prefix="/orgs", tags=["orgs"])


@router.post("", status_code=201)
async def create_org(
    body: CreateOrgRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[TenancyService, Depends(get_tenancy_service)],
) -> MeOut:
    """Create an organization for a user who does not belong to one yet."""
    return await service.create_org(user, name=body.name)


@router.get("/me")
async def get_my_org(
    user: Annotated[CurrentUser, Depends(get_current_org)],
    service: Annotated[TenancyService, Depends(get_tenancy_service)],
) -> MeOut:
    return await service.get_me(user)


@router.get("/members")
async def list_members(
    user: Annotated[CurrentUser, Depends(get_current_org)],
    service: Annotated[TenancyService, Depends(get_tenancy_service)],
) -> MembersOut:
    return await service.list_members(user)


@router.patch("/members/{member_id}/role")
async def change_member_role(
    member_id: UUID,
    body: RoleUpdateRequest,
    user: Annotated[CurrentUser, Depends(require_role("owner"))],
    service: Annotated[TenancyService, Depends(get_tenancy_service)],
) -> MemberOut:
    return await service.change_member_role(user, member_id=member_id, role=body.role)


@router.post("/invites", status_code=201)
async def create_invite(
    body: InviteCreateRequest,
    user: Annotated[CurrentUser, Depends(require_role("owner"))],
    service: Annotated[TenancyService, Depends(get_tenancy_service)],
) -> InviteOut:
    """Owner-only. The response carries the raw token exactly once."""
    return await service.create_invite(user, email=body.email, role=body.role)


@router.get("/invites")
async def list_invites(
    user: Annotated[CurrentUser, Depends(require_role("owner"))],
    service: Annotated[TenancyService, Depends(get_tenancy_service)],
) -> InvitesOut:
    return InvitesOut(invites=await service.list_invites(user))


@router.post("/invites/accept")
async def accept_invite(
    body: InviteAcceptRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[TenancyService, Depends(get_tenancy_service)],
) -> MeOut:
    """Join the inviting organization (for users not yet in one)."""
    return await service.accept_invite(user, token=body.token)
