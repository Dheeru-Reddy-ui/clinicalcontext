"""Schemas for auth bootstrap, organizations, members, and invites."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

OrgRole = Literal["owner", "clinician", "viewer"]
OrgPlan = Literal["free", "pro", "enterprise"]


class OrgOut(BaseModel):
    id: UUID
    name: str
    slug: str
    plan: OrgPlan
    created_at: datetime


class MeOut(BaseModel):
    """The current user with their membership — the /orgs/me payload."""

    user_id: UUID
    email: str | None
    full_name: str | None
    specialty: str | None
    role: OrgRole
    org: OrgOut


class MemberOut(BaseModel):
    user_id: UUID
    email: str | None
    full_name: str | None
    specialty: str | None
    role: OrgRole
    joined_at: datetime


class MembersOut(BaseModel):
    members: list[MemberOut]


class BootstrapRequest(BaseModel):
    """First-login provisioning: create an org, or join one via invite."""

    org_name: str | None = Field(default=None, min_length=1, max_length=200)
    invite_token: str | None = Field(default=None, min_length=16, max_length=128)


class CreateOrgRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class InviteCreateRequest(BaseModel):
    email: EmailStr
    role: OrgRole = "clinician"


class InviteOut(BaseModel):
    id: UUID
    email: str
    role: OrgRole
    # The raw token is returned exactly once, at creation, so the inviter can
    # copy the link. Listings return token=None.
    token: str | None
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime


class InvitesOut(BaseModel):
    invites: list[InviteOut]


class InviteAcceptRequest(BaseModel):
    token: str = Field(min_length=16, max_length=128)


class RoleUpdateRequest(BaseModel):
    role: OrgRole
