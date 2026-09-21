"""Schemas for the liveness and readiness endpoints."""

from typing import Literal

from pydantic import BaseModel

CheckStatus = Literal["ok", "error"]


class HealthResponse(BaseModel):
    """Liveness: the process is up and serving requests."""

    status: Literal["ok"] = "ok"
    service: str = "clinicalcontext-backend"
    version: str


class ReadinessCheck(BaseModel):
    """Result of probing one dependency."""

    status: CheckStatus
    detail: str | None = None
    latency_ms: float | None = None


class ReadinessResponse(BaseModel):
    """Readiness: whether the process can do real work right now."""

    status: Literal["ok", "degraded"]
    checks: dict[str, ReadinessCheck]
