"""Global daily token budget for the public demo (protects the free Groq quota).

Checked before every LLM call; successful calls add their tokens. Cache hits cost nothing.
"""

from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError

from brand_api.llm.errors import AllModelsFailedError
from brand_shared.logging_setup import get_logger

log = get_logger(__name__)


class BudgetExhaustedError(AllModelsFailedError):
    user_message = "Дневной лимит демо исчерпан. Попробуйте завтра — лимит обновляется в 00:00 UTC."

    def __init__(self) -> None:
        super().__init__([])


class TokenBudget:
    def __init__(self, redis: Redis, daily_limit: int | None) -> None:
        self.redis = redis
        self.daily_limit = daily_limit

    @staticmethod
    def _key() -> str:
        return f"budget:tokens:{datetime.now(UTC).date().isoformat()}"

    async def used(self) -> int:
        try:
            return int(await self.redis.get(self._key()) or 0)
        except RedisError:
            return 0

    async def check(self) -> None:
        if self.daily_limit is not None and await self.used() >= self.daily_limit:
            log.warning("daily_token_budget_exhausted", limit=self.daily_limit)
            raise BudgetExhaustedError()

    async def consume(self, tokens: int) -> None:
        if self.daily_limit is None or tokens <= 0:
            return
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.incrby(self._key(), tokens)
                pipe.expire(self._key(), 2 * 24 * 3600, nx=True)
                await pipe.execute()
        except RedisError as exc:
            log.warning("token_budget_unavailable", error=repr(exc))
