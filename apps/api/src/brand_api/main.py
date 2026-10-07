from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from brand_api.agent.mcp_client import McpGateway
from brand_api.agent.orchestrator import Agent
from brand_api.config import Settings, get_settings
from brand_api.db import create_engine, create_sessionmaker
from brand_api.llm.base import LLMProvider
from brand_api.llm.registry import build_router
from brand_api.llm.types import Tier
from brand_api.logging_setup import configure_logging, get_logger
from brand_api.middleware import RequestContextMiddleware
from brand_api.observability.llm_calls import DbCallRecorder
from brand_api.routes import admin, auth, clients, conversations, documents, health, plans

log = get_logger(__name__)


def create_app(
    settings: Settings | None = None, llm_providers: Mapping[str, LLMProvider] | None = None
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        redis = Redis.from_url(settings.redis_url, decode_responses=True, health_check_interval=30)
        app.state.settings = settings
        app.state.engine = engine
        sessionmaker = create_sessionmaker(engine)
        llm = build_router(settings, DbCallRecorder(sessionmaker), llm_providers)
        mcp = McpGateway(
            settings.mcp_url,
            settings.mcp_internal_secret.get_secret_value(),
            llm,
            read_timeout_s=settings.agent.generation_timeout_s,
        )
        app.state.agent = Agent(llm, mcp, settings.agent)
        app.state.sessionmaker = sessionmaker
        app.state.redis = redis
        app.state.llm = llm
        log.info(
            "startup",
            env=settings.app_env.value,
            version=settings.app_version,
            llm_provider=settings.llm.provider,
            llm_chain_complex=[str(ref) for ref in llm.chain(Tier.COMPLEX)],
        )
        try:
            yield
        finally:
            await llm.aclose()
            await redis.aclose()
            await engine.dispose()
            log.info("shutdown")

    app = FastAPI(title="Brand Assistant API", version=settings.app_version, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )
    # Added last so it wraps everything, including CORS preflight responses.
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(clients.router)
    app.include_router(conversations.router)
    app.include_router(plans.router)
    app.include_router(documents.router)
    app.include_router(admin.router)
    return app


app = create_app()
