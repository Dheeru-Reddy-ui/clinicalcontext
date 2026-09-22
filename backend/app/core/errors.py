"""Typed error hierarchy, mapped to HTTP responses in exactly one place.

Any layer raises a ``ClinicalContextError`` subclass; the exception handlers
registered here turn it into a consistent JSON body::

    {"error": {"code": "...", "message": "...", "request_id": "..."}}

Nothing else in the codebase constructs error responses.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

logger = structlog.stdlib.get_logger("app.errors")


class ClinicalContextError(Exception):
    """Base class for all domain errors."""

    status_code: int = 500
    error_code: str = "internal_error"
    default_message: str = "An internal error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        self.message = message if message is not None else self.default_message
        self.context: dict[str, Any] = dict(context or {})
        super().__init__(self.message)


class NotFoundError(ClinicalContextError):
    status_code = 404
    error_code = "not_found"
    default_message = "The requested resource was not found."


class InvalidRequestError(ClinicalContextError):
    status_code = 400
    error_code = "invalid_request"
    default_message = "The request was not valid."


class EmailExistsError(ClinicalContextError):
    """Sign-up with an address that already has an account.

    Deliberately distinguishable: the person typing their own email is told
    plainly, rather than left to guess, which is the behaviour the product
    asks for. It does mean the endpoint confirms whether an address is
    registered, so it is rate limited per address as well as per client.
    """

    status_code = 409
    error_code = "email_exists"
    default_message = "An account with this email already exists."


class AuthError(ClinicalContextError):
    status_code = 401
    error_code = "auth_error"
    default_message = "Authentication failed or credentials are missing."


class TenantIsolationError(ClinicalContextError):
    status_code = 403
    error_code = "tenant_isolation_error"
    default_message = "Access to a resource outside the caller's tenant was denied."


class PermissionDeniedError(ClinicalContextError):
    status_code = 403
    error_code = "permission_denied"
    default_message = "The caller's role does not permit this action."


class OrgMembershipRequiredError(ClinicalContextError):
    status_code = 403
    error_code = "org_membership_required"
    default_message = "This action requires membership in an organization."


class ServiceUnavailableError(ClinicalContextError):
    status_code = 503
    error_code = "service_unavailable"
    default_message = "A required backing service is unavailable."


class RateLimitError(ClinicalContextError):
    status_code = 429
    error_code = "rate_limited"
    default_message = "Rate limit exceeded. Retry after the indicated interval."

    def __init__(self, message: str | None = None, *, retry_after: int = 1) -> None:
        super().__init__(message, context={"retry_after": retry_after})
        self.retry_after = retry_after


class GuardrailBlockedError(ClinicalContextError):
    status_code = 422
    error_code = "guardrail_blocked"
    default_message = "The request was blocked by a safety guardrail."


class RetrievalError(ClinicalContextError):
    status_code = 500
    error_code = "retrieval_error"
    default_message = "The retrieval pipeline failed."


class LLMError(ClinicalContextError):
    status_code = 502
    error_code = "llm_error"
    default_message = "An upstream model call failed."


def _request_id_of(request: Request) -> str | None:
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, str) else None


def _error_body(*, code: str, message: str, request_id: str | None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "request_id": request_id}}


async def clinical_context_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map a ClinicalContextError to its HTTP response."""
    if not isinstance(exc, ClinicalContextError):  # pragma: no cover — registration guarantees it
        return await unhandled_error_handler(request, exc)

    log = logger.error if exc.status_code >= 500 else logger.warning
    log(
        "request_error",
        error_code=exc.error_code,
        status_code=exc.status_code,
        error_message=exc.message,
        **exc.context,
    )
    headers: dict[str, str] = {}
    if isinstance(exc, RateLimitError):
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(
            code=exc.error_code,
            message=exc.message,
            request_id=_request_id_of(request),
        ),
        headers=headers,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all: log the real exception, return a sanitized 500."""
    logger.exception("unhandled_error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content=_error_body(
            code="internal_error",
            message="An internal error occurred.",
            request_id=_request_id_of(request),
        ),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Install the two handlers. Called once from the app factory."""
    app.add_exception_handler(ClinicalContextError, clinical_context_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
