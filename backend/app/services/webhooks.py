"""Outbound webhooks: registration, HMAC signing, delivery with retries.

Events are **queued, not sent inline**: the request path writes a
``webhook_deliveries`` row and returns, and a worker drains the queue. A
tenant's slow or dead endpoint therefore cannot add latency to — or fail — the
query that triggered the event.

Every request carries ``X-ClinicalContext-Signature: t=<unix>,v1=<hex>`` where
hex is HMAC-SHA256 of ``"{t}.{body}"`` under the webhook's secret. The
timestamp is inside the signed material so a captured payload cannot be
replayed later, and receivers can reject stale deliveries.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
import structlog

from app.repositories.base import PgConnection, tenant_connection
from app.schemas.webhooks import (
    DeliveriesOut,
    DeliveryOut,
    WebhookCreatedOut,
    WebhookOut,
)

if TYPE_CHECKING:
    import asyncpg

    DbPool = asyncpg.Pool[asyncpg.Record]
else:
    import asyncpg

    DbPool = asyncpg.Pool

logger = structlog.stdlib.get_logger("app.services.webhooks")

SIGNATURE_HEADER = "X-ClinicalContext-Signature"
MAX_ATTEMPTS = 6
_TIMEOUT_SECONDS = 10.0
# Exponential backoff per attempt: 1m, 5m, 25m, ~2h, ~10h — then give up.
_BACKOFF_BASE_SECONDS = 60
_BACKOFF_FACTOR = 5


def sign_payload(secret: str, body: str, *, timestamp: int | None = None) -> tuple[int, str]:
    """Return (timestamp, hex digest) for a payload body."""
    stamp = timestamp if timestamp is not None else int(time.time())
    digest = hmac.new(
        secret.encode("utf-8"), f"{stamp}.{body}".encode(), hashlib.sha256
    ).hexdigest()
    return stamp, digest


def signature_header(secret: str, body: str, *, timestamp: int | None = None) -> str:
    stamp, digest = sign_payload(secret, body, timestamp=timestamp)
    return f"t={stamp},v1={digest}"


def verify_signature(secret: str, body: str, header: str, *, tolerance_seconds: int = 300) -> bool:
    """Verify a signature header. Provided so receivers (and our tests) use
    exactly the scheme we document."""
    parts = dict(piece.split("=", 1) for piece in header.split(",") if "=" in piece)
    try:
        stamp = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs(time.time() - stamp) > tolerance_seconds:
        return False
    _, expected = sign_payload(secret, body, timestamp=stamp)
    return hmac.compare_digest(expected, parts.get("v1", ""))


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=_BACKOFF_BASE_SECONDS * (_BACKOFF_FACTOR ** max(attempts - 1, 0)))


async def enqueue_event(
    conn: PgConnection, *, org_id: UUID, event: str, payload: dict[str, Any]
) -> int:
    """Queue one delivery per enabled webhook subscribed to ``event``.

    Runs on whatever connection the caller is already using, so the deliveries
    commit with the work that produced them — an event is never queued for a
    transaction that later rolls back.
    """
    queued = await conn.fetchval(
        """
        WITH targets AS (
            SELECT id FROM public.webhooks
            WHERE org_id = $1 AND enabled AND $2 = ANY(events)
        ), inserted AS (
            INSERT INTO public.webhook_deliveries
                (webhook_id, org_id, event, payload, status, next_attempt_at)
            SELECT id, $1, $2, $3::jsonb, 'pending', now() FROM targets
            RETURNING 1
        )
        SELECT count(*) FROM inserted
        """,
        org_id,
        event,
        json.dumps(payload),
    )
    return int(queued or 0)


class WebhookService:
    def __init__(self, pool: DbPool) -> None:
        self._pool = pool

    async def create(
        self, *, org_id: UUID, user_id: UUID, url: str, events: list[str]
    ) -> WebhookCreatedOut:
        secret = f"whsec_{secrets.token_urlsafe(32)}"
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            row = await conn.fetchrow(
                "INSERT INTO public.webhooks (org_id, url, secret, events, created_by) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING id, url, events, enabled, created_at",
                org_id,
                url,
                secret,
                list(events),
                user_id,
            )
        assert row is not None
        return WebhookCreatedOut(
            id=row["id"],
            url=row["url"],
            events=list(row["events"]),
            enabled=row["enabled"],
            created_at=row["created_at"],
            secret=secret,
        )

    async def list_webhooks(self, *, org_id: UUID, user_id: UUID) -> list[WebhookOut]:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            rows = await conn.fetch(
                "SELECT id, url, events, enabled, created_at FROM public.webhooks "
                "WHERE org_id = $1 ORDER BY created_at DESC",
                org_id,
            )
        return [
            WebhookOut(
                id=r["id"],
                url=r["url"],
                events=list(r["events"]),
                enabled=r["enabled"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def delete(self, *, org_id: UUID, user_id: UUID, webhook_id: UUID) -> bool:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            status = await conn.execute(
                "DELETE FROM public.webhooks WHERE id = $1 AND org_id = $2", webhook_id, org_id
            )
        return status.endswith("1")

    async def deliveries(
        self, *, org_id: UUID, user_id: UUID, limit: int, offset: int
    ) -> DeliveriesOut:
        async with tenant_connection(self._pool, org_id, user_id) as conn:
            total = await conn.fetchval(
                "SELECT count(*) FROM public.webhook_deliveries WHERE org_id = $1", org_id
            )
            rows = await conn.fetch(
                "SELECT * FROM public.webhook_deliveries WHERE org_id = $1 "
                "ORDER BY created_at DESC LIMIT $2 OFFSET $3",
                org_id,
                limit,
                offset,
            )
        return DeliveriesOut(
            deliveries=[
                DeliveryOut(
                    id=r["id"],
                    webhook_id=r["webhook_id"],
                    event=r["event"],
                    status=r["status"],
                    attempts=r["attempts"],
                    response_code=r["response_code"],
                    last_error=r["last_error"],
                    next_attempt_at=r["next_attempt_at"],
                    created_at=r["created_at"],
                    delivered_at=r["delivered_at"],
                    payload=(
                        json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
                    ),
                )
                for r in rows
            ],
            total=int(total or 0),
        )


async def deliver_pending(
    pool: DbPool, *, limit: int = 50, client: httpx.AsyncClient | None = None
) -> dict[str, int]:
    """Attempt every delivery that is due. Returns a {delivered, retried, failed} tally.

    Service context: the worker delivers for every tenant, and
    webhook_deliveries is deliberately read-only to tenants.
    """
    owned_client = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)
    tally = {"delivered": 0, "retried": 0, "failed": 0}
    try:
        async with pool.acquire() as conn:
            due = await conn.fetch(
                """
                SELECT d.id, d.event, d.payload, d.attempts, w.url, w.secret
                FROM public.webhook_deliveries d
                JOIN public.webhooks w ON w.id = d.webhook_id
                WHERE d.status = 'pending'
                  AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= now())
                  AND w.enabled
                ORDER BY d.next_attempt_at NULLS FIRST
                LIMIT $1
                """,
                limit,
            )
            for row in due:
                payload = row["payload"]
                body = json.dumps(
                    {
                        "event": row["event"],
                        "delivery_id": str(row["id"]),
                        "data": json.loads(payload) if isinstance(payload, str) else payload,
                    },
                    separators=(",", ":"),
                )
                attempts = int(row["attempts"]) + 1
                outcome = await _attempt(http, url=row["url"], secret=row["secret"], body=body)
                if outcome.ok:
                    tally["delivered"] += 1
                    await conn.execute(
                        "UPDATE public.webhook_deliveries SET status = 'delivered', "
                        "attempts = $2, response_code = $3, delivered_at = now(), "
                        "next_attempt_at = NULL, last_error = NULL WHERE id = $1",
                        row["id"],
                        attempts,
                        outcome.status_code,
                    )
                    continue
                exhausted = attempts >= MAX_ATTEMPTS
                tally["failed" if exhausted else "retried"] += 1
                await conn.execute(
                    "UPDATE public.webhook_deliveries SET status = $2, attempts = $3, "
                    "response_code = $4, last_error = $5, next_attempt_at = $6 WHERE id = $1",
                    row["id"],
                    "failed" if exhausted else "pending",
                    attempts,
                    outcome.status_code,
                    outcome.error[:2000] if outcome.error else None,
                    None if exhausted else datetime.now(tz=UTC) + _backoff(attempts),
                )
                logger.warning(
                    "webhook_delivery_failed",
                    delivery_id=str(row["id"]),
                    attempts=attempts,
                    exhausted=exhausted,
                    status_code=outcome.status_code,
                )
    finally:
        if owned_client:
            await http.aclose()
    return tally


class _Outcome:
    __slots__ = ("error", "ok", "status_code")

    def __init__(self, *, ok: bool, status_code: int | None, error: str | None) -> None:
        self.ok = ok
        self.status_code = status_code
        self.error = error


async def _attempt(client: httpx.AsyncClient, *, url: str, secret: str, body: str) -> _Outcome:
    try:
        response = await client.post(
            url,
            content=body,
            headers={
                "Content-Type": "application/json",
                SIGNATURE_HEADER: signature_header(secret, body),
            },
        )
    except httpx.HTTPError as exc:
        return _Outcome(ok=False, status_code=None, error=f"{type(exc).__name__}: {exc}")
    if 200 <= response.status_code < 300:
        return _Outcome(ok=True, status_code=response.status_code, error=None)
    return _Outcome(ok=False, status_code=response.status_code, error=response.text[:500])


async def drain_forever(pool: DbPool, *, interval_seconds: float, limit: int = 100) -> None:
    """Deliver due webhooks every ``interval_seconds`` until cancelled.

    The in-process form of the worker's drain, for a platform without a
    separate worker process. One shared HTTP client across ticks, one tick at
    a time, and a tick that raises is logged and does not end the loop —
    a tenant's broken endpoint must never stop everyone else's deliveries.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        while True:
            try:
                tally = await deliver_pending(pool, limit=limit, client=client)
                if any(tally.values()):
                    logger.info("webhook_drain_tick", **tally)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("webhook_drain_failed", error=f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(interval_seconds)
