import asyncio
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text

from brand_api.deps import EngineDep, RedisDep, SettingsDep
from brand_api.logging_setup import get_logger

router = APIRouter(tags=["health"])
log = get_logger(__name__)

CHECK_TIMEOUT_S = 5.0

CheckStatus = Literal["ok", "error"]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    env: str
    version: str
    checks: dict[str, CheckStatus]


@router.get("/health")
async def health(
    response: Response, settings: SettingsDep, engine: EngineDep, redis: RedisDep
) -> HealthResponse:
    """Readiness probe for Render/compose: verifies Postgres and Redis connectivity."""

    async def check_db() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def check_redis() -> None:
        await redis.ping()

    checks: dict[str, CheckStatus] = {}
    for name, probe in (("database", check_db), ("redis", check_redis)):
        try:
            async with asyncio.timeout(CHECK_TIMEOUT_S):
                await probe()
            checks[name] = "ok"
        except Exception as exc:
            log.warning("health_check_failed", check=name, error=repr(exc))
            checks[name] = "error"

    healthy = all(value == "ok" for value in checks.values())
    if not healthy:
        response.status_code = 503
    return HealthResponse(
        status="ok" if healthy else "degraded",
        env=settings.app_env.value,
        version=settings.app_version,
        checks=checks,
    )
