"""Per-tenant / per-user / per-API-key rate limiting (Redis fixed-window).

Limits are plan-tiered. A request consults every applicable bucket (tenant,
and either the user or the API key); the most restrictive one that trips wins.
On limit, the caller raises 429 with ``Retry-After`` = the bucket's TTL.

Fixed-window counters (INCR + EXPIRE) are used deliberately: they are atomic,
need no Lua, and are more than adequate here — the exact algorithm is not the
point, plan-tiered enforcement is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import Depends, Request

from app.core.deps import get_redis_client
from app.core.errors import RateLimitError
from app.core.security import CurrentUser, get_current_org

if TYPE_CHECKING:
    from redis.asyncio import Redis

Principal = Literal["tenant", "user", "api_key"]
_WINDOW_SECONDS = 60

# requests per minute, by plan. API keys get a separate (typically higher) tier.
_PLAN_LIMITS: dict[str, dict[Principal, int]] = {
    "free": {"tenant": 30, "user": 20, "api_key": 60},
    "pro": {"tenant": 300, "user": 120, "api_key": 600},
    "enterprise": {"tenant": 2000, "user": 600, "api_key": 3000},
}


@dataclass(slots=True)
class RateLimitStatus:
    allowed: bool
    principal: Principal | None = None
    limit: int = 0
    retry_after: int = 0


class RateLimiter:
    def __init__(self, redis: Redis, *, window_seconds: int = _WINDOW_SECONDS) -> None:
        self._redis = redis
        self._window = window_seconds

    def limit_for(self, plan: str, principal: Principal) -> int:
        return _PLAN_LIMITS.get(plan, _PLAN_LIMITS["free"])[principal]

    async def hit(self, key: str, limit: int) -> tuple[bool, int]:
        """Increment the window counter; return (within_limit, retry_after)."""
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, self._window)
        if count > limit:
            ttl = await self._redis.ttl(key)
            return False, max(ttl, 1)
        return True, 0

    async def check(
        self,
        *,
        plan: str,
        tenant_id: str,
        user_id: str | None = None,
        api_key_id: str | None = None,
    ) -> RateLimitStatus:
        """Check tenant + (user | api_key) buckets. First trip wins."""
        buckets: list[tuple[Principal, str]] = [("tenant", f"rl:t:{tenant_id}")]
        if api_key_id is not None:
            buckets.append(("api_key", f"rl:k:{api_key_id}"))
        elif user_id is not None:
            buckets.append(("user", f"rl:u:{user_id}"))

        for principal, key in buckets:
            limit = self.limit_for(plan, principal)
            ok, retry_after = await self.hit(key, limit)
            if not ok:
                return RateLimitStatus(False, principal, limit, retry_after)
        return RateLimitStatus(True)


async def enforce_rate_limit(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_org)],
    redis: Annotated[Any, Depends(get_redis_client)],
) -> CurrentUser:
    """Dependency: enforce the caller's plan-tiered limits, or raise 429.

    API-key traffic is limited on its own bucket, separate from user traffic.
    Returns the org-scoped user so endpoints can reuse it.
    """
    assert user.org_id is not None
    status = await RateLimiter(redis).check(
        plan=user.plan,
        tenant_id=str(user.org_id),
        user_id=None if user.auth_kind == "api_key" else str(user.user_id),
        api_key_id=str(user.api_key_id) if user.api_key_id else None,
    )
    if not status.allowed:
        request.state.rate_limited = True
        raise RateLimitError(
            f"{status.principal} rate limit of {status.limit}/min exceeded",
            retry_after=status.retry_after,
        )
    return user
