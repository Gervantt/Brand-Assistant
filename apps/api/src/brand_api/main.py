from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from brand_api.config import Settings, get_settings
from brand_api.db import create_engine, create_sessionmaker
from brand_api.logging_setup import configure_logging, get_logger
from brand_api.middleware import RequestContextMiddleware
from brand_api.routes import health

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        redis = Redis.from_url(settings.redis_url, decode_responses=True, health_check_interval=30)
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        app.state.redis = redis
        log.info("startup", env=settings.app_env.value, version=settings.app_version)
        try:
            yield
        finally:
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
    return app


app = create_app()
