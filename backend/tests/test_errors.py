"""Error hierarchy: every typed error maps to its HTTP status and JSON shape."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.errors import (
    AuthError,
    ClinicalContextError,
    GuardrailBlockedError,
    LLMError,
    NotFoundError,
    OrgMembershipRequiredError,
    PermissionDeniedError,
    RetrievalError,
    ServiceUnavailableError,
    TenantIsolationError,
)
from app.main import create_app

ERROR_CASES = [
    (NotFoundError, 404, "not_found"),
    (AuthError, 401, "auth_error"),
    (TenantIsolationError, 403, "tenant_isolation_error"),
    (PermissionDeniedError, 403, "permission_denied"),
    (OrgMembershipRequiredError, 403, "org_membership_required"),
    (GuardrailBlockedError, 422, "guardrail_blocked"),
    (RetrievalError, 500, "retrieval_error"),
    (LLMError, 502, "llm_error"),
    (ServiceUnavailableError, 503, "service_unavailable"),
]


def _make_raising_route(
    exc_class: type[ClinicalContextError],
) -> Callable[[], Coroutine[Any, Any, None]]:
    async def route() -> None:
        raise exc_class()

    return route


async def _boom() -> None:
    raise RuntimeError("secret internal detail that must not leak")


@pytest.fixture
async def error_client() -> AsyncIterator[AsyncClient]:
    app = create_app()
    for exc_class, _, _ in ERROR_CASES:
        app.get(f"/_test/{exc_class.__name__}")(_make_raising_route(exc_class))
    app.get("/_test/unhandled")(_boom)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


@pytest.mark.parametrize(("exc_class", "expected_status", "expected_code"), ERROR_CASES)
async def test_typed_errors_map_to_http(
    error_client: AsyncClient,
    exc_class: type[ClinicalContextError],
    expected_status: int,
    expected_code: str,
) -> None:
    response = await error_client.get(f"/_test/{exc_class.__name__}")

    assert response.status_code == expected_status
    body = response.json()
    assert body["error"]["code"] == expected_code
    assert body["error"]["message"] == exc_class.default_message
    assert body["error"]["request_id"], "request_id must be present in error bodies"


async def test_unhandled_errors_return_sanitized_500(error_client: AsyncClient) -> None:
    response = await error_client.get("/_test/unhandled")

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert "secret internal detail" not in response.text


async def test_custom_message_overrides_default(error_client: AsyncClient) -> None:
    assert NotFoundError("document 42 not found").message == "document 42 not found"
    assert NotFoundError().message == NotFoundError.default_message
