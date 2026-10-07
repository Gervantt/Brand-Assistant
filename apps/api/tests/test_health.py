from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager

from brand_api.config import Settings
from brand_api.main import create_app


async def test_health_ok_when_dependencies_are_up(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"database": "ok", "redis": "ok"}


async def test_request_id_is_generated_and_echoed(client: httpx.AsyncClient) -> None:
    generated = await client.get("/health")
    assert len(generated.headers["x-request-id"]) == 32

    echoed = await client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["x-request-id"] == "abc-123"


async def test_unsafe_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"})
    assert response.headers["x-request-id"] != "bad id\nwith newline"


@pytest.fixture
async def client_without_redis(
    settings: Settings, migrated_db: str
) -> AsyncIterator[httpx.AsyncClient]:
    broken = settings.model_copy(update={"redis_url": "redis://localhost:1/0"})
    app = create_app(broken)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http,
    ):
        yield http


async def test_health_degraded_when_redis_is_down(
    client_without_redis: httpx.AsyncClient,
) -> None:
    response = await client_without_redis.get("/health")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "redis": "error"}
