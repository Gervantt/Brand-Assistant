"""FastAPI dependencies. Shared resources live on `app.state`, created in the lifespan."""

from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from brand_api.config import Settings
from brand_api.llm.router import LLMRouter


def get_app_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_engine(request: Request) -> AsyncEngine:
    return cast(AsyncEngine, request.app.state.engine)


def get_redis(request: Request) -> Redis:
    return cast(Redis, request.app.state.redis)


def get_llm(request: Request) -> LLMRouter:
    return cast(LLMRouter, request.app.state.llm)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    sessionmaker = cast(async_sessionmaker[AsyncSession], request.app.state.sessionmaker)
    async with sessionmaker() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
EngineDep = Annotated[AsyncEngine, Depends(get_engine)]
RedisDep = Annotated[Redis, Depends(get_redis)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
LLMDep = Annotated[LLMRouter, Depends(get_llm)]
