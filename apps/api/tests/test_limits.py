"""Public-demo protection: per-user agent rate limit, login throttling, daily token budget."""

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from redis.asyncio import Redis

from brand_api.config import Settings
from brand_api.limits.budget import BudgetExhaustedError, TokenBudget
from brand_api.limits.rate_limit import RateLimiter
from brand_api.llm.config import LLMSettings
from brand_api.llm.router import LLMRouter
from brand_api.llm.types import ChatRequest, Message, Tier
from brand_api.main import create_app
from brand_shared.permissions import Role
from tests.agent_helpers import chat, new_conversation
from tests.conftest import TEST_REDIS_URL, demo_headers
from tests.fakes import ScriptedProvider, reply
from tests.test_router import ListRecorder


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield client
    await client.aclose()


async def test_rate_limiter_counts_per_key(redis: Redis) -> None:
    limiter = RateLimiter(redis, limit=2, window_s=3600, prefix=f"rl:test:{uuid.uuid4().hex}")
    first, second, third = [await limiter.hit("user-a") for _ in range(3)]
    other = await limiter.hit("user-b")
    assert (first.allowed, second.allowed, third.allowed) == (True, True, False)
    assert third.remaining == 0
    assert 0 < third.retry_after_s <= 3601
    assert other.allowed


@pytest.fixture
async def strict_client(
    settings: Settings, seeded: None
) -> AsyncIterator[tuple[httpx.AsyncClient, ScriptedProvider]]:
    llm = ScriptedProvider("groq", [reply("ок"), reply("ок")])
    strict = settings.model_copy(update={"rate_limit_per_hour": 2, "login_attempts_per_15m": 3})
    app = create_app(strict, llm_providers={"groq": llm})
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client,
    ):
        yield client, llm


async def fresh_viewer(client: httpx.AsyncClient) -> dict[str, str]:
    """Self-registered user + Bean There access granted by the admin (a clean rate-limit key)."""
    email = f"rl-{uuid.uuid4().hex[:10]}@example.com"
    registered = await client.post(
        "/auth/register", json={"email": email, "password": "s3cret-pass"}
    )
    admin = await demo_headers(client, Role.ADMIN)
    clients = (await client.get("/clients", headers=admin)).json()
    bean_there = next(c["id"] for c in clients if c["slug"] == "bean-there")
    await client.patch(
        f"/admin/users/{registered.json()['user']['id']}",
        json={"client_ids": [bean_there]},
        headers=admin,
    )
    return {"Authorization": f"Bearer {registered.json()['access_token']}"}


async def test_agent_requests_are_limited_per_user(
    strict_client: tuple[httpx.AsyncClient, ScriptedProvider],
) -> None:
    client, _ = strict_client
    headers = await fresh_viewer(client)
    conversation_id = await new_conversation(client, headers)
    await chat(client, headers, conversation_id, "раз")
    await chat(client, headers, conversation_id, "два")

    third = await client.post(
        f"/conversations/{conversation_id}/messages", json={"content": "три"}, headers=headers
    )
    assert third.status_code == 429
    assert "2 запросов" in third.json()["detail"]
    assert int(third.headers["retry-after"]) > 0


async def test_login_attempts_are_throttled(
    strict_client: tuple[httpx.AsyncClient, ScriptedProvider],
) -> None:
    client, _ = strict_client
    email = f"brute-{uuid.uuid4().hex[:8]}@example.com"
    codes = [
        (await client.post("/auth/login", json={"email": email, "password": "x"})).status_code
        for _ in range(4)
    ]
    assert codes == [401, 401, 401, 429]


async def test_token_budget_blocks_calls_once_spent(redis: Redis) -> None:
    budget = TokenBudget(redis, daily_limit=1000)
    await redis.delete(budget._key())
    groq = ScriptedProvider("groq", [reply(tokens_in=900, tokens_out=200)])
    router = LLMRouter(LLMSettings(provider="groq"), {"groq": groq}, ListRecorder(), budget=budget)
    question = ChatRequest(messages=[Message(role="user", content="x")])

    await router.chat(question, tier=Tier.SIMPLE)
    assert await budget.used() == 1100

    with pytest.raises(BudgetExhaustedError) as exc_info:
        await router.chat(question, tier=Tier.SIMPLE)
    assert "лимит" in exc_info.value.user_message
    assert len(groq.requests) == 1  # the second call never reached the provider
    await redis.delete(budget._key())
