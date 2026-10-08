"""Fixed-window rate limiting in Redis (per user for the agent, per IP+email for login)."""

import time
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from brand_shared.logging_setup import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after_s: int


class RateLimiter:
    def __init__(self, redis: Redis, *, limit: int, window_s: int, prefix: str) -> None:
        self.redis = redis
        self.limit = limit
        self.window_s = window_s
        self.prefix = prefix

    async def hit(self, key: str) -> RateLimitResult:
        now = time.time()
        bucket = int(now // self.window_s)
        retry_after = int(self.window_s - (now % self.window_s)) + 1
        redis_key = f"{self.prefix}:{key}:{bucket}"
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.incr(redis_key)
                pipe.expire(redis_key, self.window_s + 60, nx=True)
                count, _ = await pipe.execute()
        except RedisError as exc:  # fail open: a cache outage must not lock users out
            log.warning("rate_limit_unavailable", error=repr(exc))
            return RateLimitResult(True, self.limit, self.limit, 0)
        count = int(count)
        return RateLimitResult(
            allowed=count <= self.limit,
            limit=self.limit,
            remaining=max(self.limit - count, 0),
            retry_after_s=retry_after,
        )
