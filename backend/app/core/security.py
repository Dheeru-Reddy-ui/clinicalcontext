"""Authentication and authorization.

Identity comes from a Supabase-issued JWT presented as a Bearer token and
verified locally: asymmetric tokens (RS256/ES256) against the project's JWKS
(fetched once and cached, refreshed on unknown ``kid``), legacy HS256 tokens
against ``SUPABASE_JWT_SECRET`` when configured. Verification is offline —
no per-request call to Supabase.

Authorization comes from the database, never the client: the profile row
keyed by the token's ``sub`` provides ``org_id`` and ``role``. A client can
therefore never choose its org — it can only prove who it is.

FastAPI dependencies exposed here:
- ``get_auth_context``   — verified token claims only (no DB).
- ``get_current_user``   — claims + profile (org/role may be absent pre-bootstrap).
- ``get_current_org``    — like above, but requires org membership.
- ``require_role(*roles)`` — org membership + role check.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal
from uuid import UUID

import httpx
import jwt
import structlog
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.core.deps import DbPool, get_asyncpg_pool
from app.core.errors import AuthError, OrgMembershipRequiredError, PermissionDeniedError
from app.core.telemetry import bind_context
from app.schemas.tenancy import OrgRole

logger = structlog.stdlib.get_logger("app.security")

_ASYMMETRIC_ALGORITHMS = frozenset({"RS256", "ES256"})
_JWKS_TTL_SECONDS = 600.0
_JWT_LEEWAY_SECONDS = 10
_AUDIENCE = "authenticated"


class AuthContext(BaseModel):
    """Verified JWT claims — identity only, no authorization data."""

    model_config = ConfigDict(frozen=True)

    user_id: UUID
    email: str | None
    org_claim: UUID | None  # informational; authorization uses the profile row
    full_name: str | None


class CurrentUser(BaseModel):
    """Identity plus the database-backed membership (absent before bootstrap).

    Produced from either a Supabase JWT or an API key; ``auth_kind`` and the
    ``api_key_*`` fields record which, so rate limiting and auditing can treat
    API-key traffic distinctly.
    """

    model_config = ConfigDict(frozen=True)

    user_id: UUID
    email: str | None
    full_name: str | None
    org_id: UUID | None
    role: OrgRole | None
    specialty: str | None
    plan: str = "free"
    auth_kind: Literal["jwt", "api_key"] = "jwt"
    api_key_id: UUID | None = None
    api_key_prefix: str | None = None
    scopes: list[str] = []

    @property
    def is_bootstrapped(self) -> bool:
        return self.org_id is not None


class JWKSCache:
    """Async JWKS fetch-and-cache with refresh on unknown key ids.

    ``preloaded`` bypasses the network entirely — used by tests to inject a
    known keypair through the exact same verification path production uses.
    """

    def __init__(
        self,
        url: str,
        *,
        ttl_seconds: float = _JWKS_TTL_SECONDS,
        preloaded: dict[str, Any] | None = None,
    ) -> None:
        self._url = url
        self._ttl = ttl_seconds
        self._static = preloaded is not None
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()
        if preloaded is not None:
            self._load(preloaded)
            self._fetched_at = time.monotonic()

    def _load(self, jwks: dict[str, Any]) -> None:
        keys: dict[str, jwt.PyJWK] = {}
        for entry in jwks.get("keys", []):
            kid = entry.get("kid")
            if not kid:
                continue
            try:
                keys[kid] = jwt.PyJWK(entry)
            except jwt.PyJWKError:  # skip unusable entries, keep the rest
                logger.warning("jwks_key_unusable", kid=kid)
        self._keys = keys

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(self._url)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        self._load(payload)
        self._fetched_at = time.monotonic()

    async def get_key(self, kid: str) -> jwt.PyJWK:
        stale = (time.monotonic() - self._fetched_at) > self._ttl
        if not self._static and (kid not in self._keys or stale):
            async with self._lock:
                # Re-check under the lock: another request may have refreshed.
                stale = (time.monotonic() - self._fetched_at) > self._ttl
                if kid not in self._keys or stale:
                    try:
                        await self._refresh()
                    except (httpx.HTTPError, ValueError) as exc:
                        logger.error("jwks_fetch_failed", url=self._url, error=str(exc))
                        raise AuthError("unable to verify token signature") from exc
        key = self._keys.get(kid)
        if key is None:
            raise AuthError("token signed with an unknown key")
        return key


class JWTVerifier:
    """Verifies Supabase access tokens. Constructed once per process."""

    def __init__(self, settings: Settings, *, jwks: JWKSCache | None = None) -> None:
        self._issuer = settings.supabase_issuer
        self._hs256_secret = (
            settings.supabase_jwt_secret.get_secret_value()
            if settings.supabase_jwt_secret is not None
            else None
        )
        self._jwks = jwks if jwks is not None else JWKSCache(settings.supabase_jwks_url)

    async def verify(self, token: str) -> AuthContext:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError("malformed token") from exc

        algorithm = header.get("alg")
        key: str | jwt.PyJWK
        if algorithm in _ASYMMETRIC_ALGORITHMS:
            kid = header.get("kid")
            if not isinstance(kid, str) or not kid:
                raise AuthError("token missing key id")
            key = await self._jwks.get_key(kid)
        elif algorithm == "HS256":
            if self._hs256_secret is None:
                raise AuthError("HS256 token received but SUPABASE_JWT_SECRET is not configured")
            key = self._hs256_secret
        else:
            raise AuthError("unsupported token algorithm")

        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key.key if isinstance(key, jwt.PyJWK) else key,
                algorithms=[str(algorithm)],
                audience=_AUDIENCE,
                issuer=self._issuer,
                leeway=_JWT_LEEWAY_SECONDS,
                options={"require": ["exp", "sub", "aud", "iss"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("token expired") from exc
        except jwt.PyJWTError as exc:
            raise AuthError("invalid token") from exc

        try:
            user_id = UUID(str(claims["sub"]))
        except (ValueError, KeyError) as exc:
            raise AuthError("token subject is not a valid user id") from exc

        return AuthContext(
            user_id=user_id,
            email=_optional_str(claims.get("email")),
            org_claim=_extract_org_claim(claims),
            full_name=_extract_full_name(claims),
        )


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _extract_org_claim(claims: dict[str, Any]) -> UUID | None:
    """Same precedence as the SQL helper app.user_org_id(): top-level claim,
    then app_metadata. Malformed values fail closed to None."""
    raw = claims.get("org_id")
    if raw is None:
        app_metadata = claims.get("app_metadata")
        if isinstance(app_metadata, dict):
            raw = app_metadata.get("org_id")
    if raw is None:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        logger.warning("malformed_org_claim", claim=str(raw))
        return None


def _extract_full_name(claims: dict[str, Any]) -> str | None:
    user_metadata = claims.get("user_metadata")
    if isinstance(user_metadata, dict):
        return _optional_str(user_metadata.get("full_name"))
    return None


# -- FastAPI dependencies -----------------------------------------------------------

_bearer = HTTPBearer(auto_error=False)


def get_jwt_verifier(request: Request) -> JWTVerifier:
    verifier = getattr(request.app.state, "jwt_verifier", None)
    if not isinstance(verifier, JWTVerifier):  # pragma: no cover — set in create_app
        raise AuthError("token verification is not configured")
    return verifier


async def get_auth_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    verifier: Annotated[JWTVerifier, Depends(get_jwt_verifier)],
) -> AuthContext:
    if credentials is None or not credentials.credentials:
        raise AuthError("missing bearer token")
    return await verifier.verify(credentials.credentials)


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    verifier: Annotated[JWTVerifier, Depends(get_jwt_verifier)],
    pool: Annotated[DbPool, Depends(get_asyncpg_pool)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> CurrentUser:
    """Resolve the caller from an API key (X-API-Key) or a Supabase JWT.

    The profile/key read runs in the service context on purpose: it is the
    trust root that *establishes* the tenant context everything else runs under.
    """
    if x_api_key:
        user = await _user_from_api_key(pool, x_api_key)
        request.state.api_key_id = str(user.api_key_id)
        request.state.api_key_prefix = user.api_key_prefix
    else:
        if credentials is None or not credentials.credentials:
            raise AuthError("missing credentials (bearer token or X-API-Key)")
        auth = await verifier.verify(credentials.credentials)
        profile = await pool.fetchrow(
            "SELECT p.org_id, p.role, p.full_name, p.specialty, o.plan "
            "FROM public.profiles p LEFT JOIN public.organizations o ON o.id = p.org_id "
            "WHERE p.id = $1",
            auth.user_id,
        )
        user = CurrentUser(
            user_id=auth.user_id,
            email=auth.email,
            full_name=profile["full_name"] if profile else auth.full_name,
            org_id=profile["org_id"] if profile else None,
            role=profile["role"] if profile else None,
            specialty=profile["specialty"] if profile else None,
            plan=profile["plan"] if profile and profile["plan"] else "free",
        )
    bind_context(
        user_id=str(user.user_id),
        tenant_id=str(user.org_id) if user.org_id else None,
        auth_kind=user.auth_kind,
    )
    return user


async def _user_from_api_key(pool: DbPool, presented: str) -> CurrentUser:
    from app.services.api_keys import resolve_api_key, scope_role

    ctx = await resolve_api_key(pool, presented)
    if ctx is None:
        raise AuthError("invalid or revoked API key")
    if ctx.created_by is None:
        raise AuthError("the API key's creator no longer exists")
    plan = await pool.fetchval("SELECT plan FROM public.organizations WHERE id = $1", ctx.org_id)
    return CurrentUser(
        user_id=ctx.created_by,
        email=None,
        full_name=None,
        org_id=ctx.org_id,
        role=scope_role(ctx.scopes),
        specialty=None,
        plan=str(plan) if plan else "free",
        auth_kind="api_key",
        api_key_id=ctx.key_id,
        api_key_prefix=ctx.prefix,
        scopes=ctx.scopes,
    )


async def get_current_org(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CurrentUser:
    """The current user, guaranteed to belong to an organization."""
    if user.org_id is None or user.role is None:
        raise OrgMembershipRequiredError()
    return user


def require_role(*roles: OrgRole) -> Callable[..., Awaitable[CurrentUser]]:
    """Dependency factory: org membership plus one of the given roles."""
    allowed = frozenset(roles)

    async def dependency(
        user: Annotated[CurrentUser, Depends(get_current_org)],
    ) -> CurrentUser:
        if user.role not in allowed:
            raise PermissionDeniedError(
                f"requires role {' or '.join(sorted(allowed))}",
                context={"actual_role": user.role},
            )
        return user

    return dependency
