from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from starlette.applications import Starlette

from brand_api.agent.mcp_client import McpGateway
from brand_api.agent.orchestrator import Agent
from brand_api.config import Settings, get_settings
from brand_api.db import create_engine, create_sessionmaker
from brand_api.limits.budget import TokenBudget
from brand_api.limits.rate_limit import RateLimiter
from brand_api.llm.base import LLMProvider
from brand_api.llm.cache import ResponseCache
from brand_api.llm.registry import build_router
from brand_api.llm.types import Tier
from brand_api.logging_setup import configure_logging, get_logger
from brand_api.middleware import RequestContextMiddleware
from brand_api.observability.llm_calls import DbCallRecorder
from brand_api.observability.tracing import Tracer
from brand_api.routes import admin, auth, clients, conversations, documents, health, plans

log = get_logger(__name__)


EMBEDDED_MCP_PREFIX = "/mcp-internal"


def _embedded_mcp(settings: Settings) -> Starlette:
    """The MCP tool server as an ASGI app inside this process (Render: one 512 MB container).

    Imported lazily so the compose API image doesn't need the RAG/embedding dependencies.
    """
    from brand_mcp.config import McpSettings  # noqa: PLC0415
    from brand_mcp.server import create_app as create_mcp_app  # noqa: PLC0415

    # Infrastructure and the shared secret come from the API's settings; RAG options (embedding
    # provider, model cache, reranker, seed directory) from the environment as usual.
    return create_mcp_app(
        McpSettings(
            app_env=settings.app_env.value,
            log_level=settings.log_level,
            log_json=settings.log_json,
            database_url=settings.database_url,
            redis_url=settings.redis_url,
            mcp_internal_secret=settings.mcp_internal_secret,
        )
    )


def create_app(
    settings: Settings | None = None, llm_providers: Mapping[str, LLMProvider] | None = None
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    mcp_app = _embedded_mcp(settings) if settings.mcp_mode == "embedded" else None
    mcp_url = (
        f"http://127.0.0.1:{settings.port}{EMBEDDED_MCP_PREFIX}/mcp"
        if mcp_app is not None
        else settings.mcp_url
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        redis = Redis.from_url(settings.redis_url, decode_responses=True, health_check_interval=30)
        app.state.settings = settings
        app.state.engine = engine
        sessionmaker = create_sessionmaker(engine)
        tracer = Tracer.create(
            public_key=settings.langfuse_public_key,
            secret_key=(
                settings.langfuse_secret_key.get_secret_value()
                if settings.langfuse_secret_key
                else None
            ),
            host=settings.langfuse_host,
            environment=settings.app_env.value,
            release=settings.app_version,
        )
        budget = TokenBudget(redis, settings.daily_token_budget)
        llm = build_router(
            settings,
            DbCallRecorder(sessionmaker),
            llm_providers,
            tracer=tracer,
            cache=ResponseCache(redis, settings.llm.cache_ttl_s),
            budget=budget,
        )
        app.state.budget = budget
        app.state.agent_limiter = RateLimiter(
            redis, limit=settings.rate_limit_per_hour, window_s=3600, prefix="rl:agent"
        )
        app.state.login_limiter = RateLimiter(
            redis, limit=settings.login_attempts_per_15m, window_s=900, prefix="rl:login"
        )
        mcp = McpGateway(
            mcp_url,
            settings.mcp_internal_secret.get_secret_value(),
            llm,
            read_timeout_s=settings.agent.generation_timeout_s,
        )
        app.state.agent = Agent(llm, mcp, settings.agent, tracer)
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
        async with AsyncExitStack() as stack:
            if mcp_app is not None:  # run the embedded server's lifespan (sessions, models)
                await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
            try:
                yield
            finally:
                await llm.aclose()
                tracer.shutdown()  # flush pending spans
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
    if mcp_app is not None:
        app.mount(EMBEDDED_MCP_PREFIX, mcp_app)
    return app


app = create_app()
