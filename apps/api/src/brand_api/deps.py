"""FastAPI dependencies. Shared resources live on `app.state`, created in the lifespan."""

from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from brand_api.agent.orchestrator import Agent
from brand_api.config import Settings
from brand_api.limits.budget import TokenBudget
from brand_api.limits.rate_limit import RateLimiter
from brand_api.llm.router import LLMRouter


def get_app_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_engine(request: Request) -> AsyncEngine:
    return cast(AsyncEngine, request.app.state.engine)


def get_redis(request: Request) -> Redis:
    return cast(Redis, request.app.state.redis)


def get_agent(request: Request) -> Agent:
    return cast(Agent, request.app.state.agent)


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
AgentDep = Annotated[Agent, Depends(get_agent)]


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    return cast(async_sessionmaker[AsyncSession], request.app.state.sessionmaker)


SessionmakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]


def get_agent_limiter(request: Request) -> RateLimiter:
    return cast(RateLimiter, request.app.state.agent_limiter)


def get_login_limiter(request: Request) -> RateLimiter:
    return cast(RateLimiter, request.app.state.login_limiter)


def get_budget(request: Request) -> TokenBudget:
    return cast(TokenBudget, request.app.state.budget)


AgentLimiterDep = Annotated[RateLimiter, Depends(get_agent_limiter)]
LoginLimiterDep = Annotated[RateLimiter, Depends(get_login_limiter)]
BudgetDep = Annotated[TokenBudget, Depends(get_budget)]
