"""The in-process webhook drain: the stand-in for a worker process on a
platform that has none (Phase 14.5, the free-tier deployment)."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from app.services import webhooks


async def test_drain_survives_a_failing_tick_and_stops_on_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One tenant's broken endpoint must not end everyone's deliveries, and a
    shutdown must be able to stop the loop promptly."""
    calls: list[int] = []

    async def fake_deliver(pool: Any, *, limit: int, client: Any) -> dict[str, int]:
        calls.append(limit)
        if len(calls) == 1:
            raise RuntimeError("tenant endpoint exploded")
        return {"delivered": 1, "retried": 0, "failed": 0}

    monkeypatch.setattr(webhooks, "deliver_pending", fake_deliver)

    task = asyncio.create_task(
        webhooks.drain_forever(cast(Any, object()), interval_seconds=0.01, limit=7)
    )
    # Building the HTTP client (TLS context) takes most of a second on a cold
    # process; wait for ticks rather than for a fixed time.
    async with asyncio.timeout(5):
        while len(calls) < 3:
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(calls) >= 3, "the loop must keep ticking after a failed tick"
    assert set(calls) == {7}, "the configured batch size reaches every tick"
