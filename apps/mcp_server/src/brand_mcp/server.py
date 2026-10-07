"""MCP tool server: Starlette app = auth gate + /health + MCP streamable HTTP at /mcp."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from brand_mcp.auth import InternalAuthMiddleware
from brand_mcp.config import McpSettings, get_settings
from brand_mcp.deps import Deps
from brand_mcp.tools import generation_tools, publish, read_tools
from brand_shared.logging_setup import configure_logging, get_logger

log = get_logger("brand_mcp.server")

INSTRUCTIONS = (
    "Инструменты ассистента креативного агентства: поиск по брендбуку, профиль клиента, "
    "генерация контент-плана, постов и брифов, публикация утверждённого плана."
)


def build_server(deps: Deps) -> MCPServer:
    server = MCPServer("brand-assistant-tools", instructions=INSTRUCTIONS, version="0.1.0")
    read_tools.register(server, deps)
    generation_tools.register(server, deps)
    publish.register(server, deps)
    return server


def create_app(settings: McpSettings | None = None, deps: Deps | None = None) -> Starlette:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    deps = deps or Deps.create(settings)
    mcp_app = build_server(deps).streamable_http_app(
        streamable_http_path="/mcp",
        # The endpoint is never browser-facing and every request must carry a signed service
        # token, so Host/Origin pinning (DNS-rebinding protection) adds nothing but config.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with mcp_app.router.lifespan_context(mcp_app):
            log.info("mcp_startup", env=settings.app_env)
            try:
                yield
            finally:
                await deps.aclose()

    return Starlette(
        routes=[Route("/health", health), Mount("/", app=mcp_app)],
        middleware=[
            Middleware(
                InternalAuthMiddleware, secret=settings.mcp_internal_secret.get_secret_value()
            )
        ],
        lifespan=lifespan,
    )


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
