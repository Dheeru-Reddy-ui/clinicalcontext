"""Security response headers (Phase 14.4).

The API serves JSON and SSE to one known origin, never HTML a browser will
render, so its headers are about denying what it does not do: no framing, no
MIME sniffing, no referrer leakage, no cross-origin embedding, and — once it
is behind TLS — HSTS.

The Content-Security-Policy here is the *API's* policy: a locked-down one
that forbids everything, which is correct for a surface that returns no
markup. The application's own CSP, the one that matters for XSS, belongs to
the frontend and lives in `frontend/next.config.ts`.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# A year, in seconds — the value browsers require before preloading.
_HSTS_MAX_AGE = 31_536_000

_ALWAYS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    # This surface returns JSON and event streams; nothing may be loaded,
    # framed or executed from it.
    "Content-Security-Policy": (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    # No Google-style interest cohort / topics participation.
    "Permissions-Policy": "camera=(), geolocation=(), interest-cohort=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach the headers above to every response.

    ``https_only`` adds HSTS. It is off in local development because a
    browser that sees HSTS on ``localhost`` will refuse plain HTTP there for a
    year — including for other projects on the same host.
    """

    def __init__(self, app: object, *, https_only: bool) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._https_only = https_only

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for header, value in _ALWAYS.items():
            response.headers.setdefault(header, value)
        if self._https_only:
            response.headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={_HSTS_MAX_AGE}; includeSubDomains",
            )
        return response


__all__ = ["SecurityHeadersMiddleware"]
