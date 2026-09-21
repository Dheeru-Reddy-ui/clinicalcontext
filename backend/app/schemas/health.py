"""Schemas for the liveness and readiness endpoints."""

from typing import Literal

from pydantic import BaseModel

CheckStatus = Literal["ok", "error"]


class HealthResponse(BaseModel):
    """Liveness: the process is up and serving requests."""

    status: Literal["ok"] = "ok"
    service: str = "clinicalcontext-backend"
    version: str
    # The deployed git revision ("dev" locally). A deploy pipeline polls this
    # to know the new code is answering, not the old process still draining.
    release: str


class ReadinessCheck(BaseModel):
    """Result of probing one dependency."""

    status: CheckStatus
    detail: str | None = None
    latency_ms: float | None = None


class ReadinessResponse(BaseModel):
    """Readiness: whether the process can do real work right now."""

    status: Literal["ok", "degraded"]
    checks: dict[str, ReadinessCheck]
