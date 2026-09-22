"""Schemas for public sign-up (see ``app/services/signup.py``)."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    # Supabase enforces the project's own password policy on top of this; its
    # complaint is passed through verbatim rather than restated here.
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=120)


class SignupOut(BaseModel):
    """The account exists and its password works.

    No session is returned: the browser signs in immediately afterwards, so
    the tokens are minted by Supabase directly into the client that will
    hold them, and this endpoint never handles a session cookie.
    """

    email: EmailStr
    created: bool = True
