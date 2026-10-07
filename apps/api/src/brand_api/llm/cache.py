"""Redis cache for identical LLM requests. Hits are recorded in `llm_calls` (cached=true,
cost 0, cost_saved = what the call would have cost), so savings show up in /admin/metrics."""

import hashlib
import json

from redis.asyncio import Redis
from redis.exceptions import RedisError

from brand_api.llm.types import ChatRequest, ChatResponse, ModelRef, Tier
from brand_shared.logging_setup import get_logger

log = get_logger(__name__)
PREFIX = "llm:cache:"


class ResponseCache:
    def __init__(self, redis: Redis, ttl_s: int) -> None:
        self.redis = redis
        self.ttl_s = ttl_s

    @staticmethod
    def key(primary: ModelRef, tier: Tier, request: ChatRequest) -> str:
        """Same model + tier + byte-identical request (messages, tools, params) -> same key."""
        payload = json.dumps(
            {"model": str(primary), "tier": tier.value, "request": request.model_dump(mode="json")},
            sort_keys=True,
            ensure_ascii=False,
        )
        return PREFIX + hashlib.sha256(payload.encode()).hexdigest()

    async def get(self, key: str) -> ChatResponse | None:
        try:
            raw = await self.redis.get(key)
        except RedisError as exc:  # the cache is an optimisation, never a dependency
            log.warning("llm_cache_unavailable", error=repr(exc))
            return None
        return ChatResponse.model_validate_json(raw) if raw else None

    async def set(self, key: str, response: ChatResponse) -> None:
        try:
            await self.redis.set(key, response.model_dump_json(), ex=self.ttl_s)
        except RedisError as exc:
            log.warning("llm_cache_unavailable", error=repr(exc))
