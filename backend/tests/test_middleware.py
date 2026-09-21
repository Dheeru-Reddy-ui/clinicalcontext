"""Request-context middleware: X-Request-ID generation and propagation."""

from __future__ import annotations

from httpx import AsyncClient


async def test_response_carries_generated_request_id(client: AsyncClient) -> None:
    response = await client.get("/health")

    request_id = response.headers.get("x-request-id")
    assert request_id is not None
    assert len(request_id) == 32  # uuid4().hex


async def test_inbound_request_id_is_propagated(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "upstream-trace-42"})

    assert response.headers["x-request-id"] == "upstream-trace-42"


async def test_each_request_gets_a_unique_id(client: AsyncClient) -> None:
    first = await client.get("/health")
    second = await client.get("/health")

    assert first.headers["x-request-id"] != second.headers["x-request-id"]


async def test_cors_exposes_the_headers_the_browser_reads(client: AsyncClient) -> None:
    """The frontend reads `X-Request-ID` (for support and Sentry correlation)
    and `Retry-After` (to say when a rate-limited request may be retried).

    Cross-origin JavaScript can only see the six CORS-safelisted response
    headers unless the server names the rest, so a header missing from
    `expose_headers` is invisible in the browser while looking fine in curl.
    `Retry-After` was missing, which blanked the UI's "try again in Ns".
    """
    response = await client.get("/health", headers={"Origin": "http://localhost:3000"})

    exposed = {
        h.strip().lower()
        for h in response.headers.get("access-control-expose-headers", "").split(",")
        if h.strip()
    }
    assert {"x-request-id", "retry-after"} <= exposed, exposed
