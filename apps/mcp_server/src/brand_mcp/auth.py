"""Two layers: an ASGI gate (no valid service token -> 401 before MCP parses anything) and a
per-tool check of the end user's role and client access, run as the first resolver so that a
denied call never triggers LLM sampling."""

import json
import uuid
from dataclasses import dataclass

import jwt
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from starlette.types import ASGIApp, Receive, Scope, Send

from brand_shared.internal_auth import (
    HEADER,
    InternalClaims,
    decode_internal_token,
    parse_bearer,
)
from brand_shared.logging_setup import get_logger
from brand_shared.permissions import Tool, can_use_tool

log = get_logger("brand_mcp.auth")


class InternalAuthMiddleware:
    def __init__(self, app: ASGIApp, *, secret: str, protected_prefix: str = "/mcp") -> None:
        self.app = app
        self.secret = secret
        self.prefix = protected_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith(self.prefix):
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            token = parse_bearer(headers.get(HEADER))
            try:
                if token is None:
                    raise jwt.InvalidTokenError("missing bearer token")
                decode_internal_token(token, secret=self.secret)
            except jwt.InvalidTokenError as exc:
                log.warning("mcp_unauthorized", path=scope["path"], reason=str(exc))
                body = json.dumps({"error": "unauthorized"}).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


@dataclass(frozen=True)
class Authorized:
    claims: InternalClaims
    client_id: uuid.UUID
    tool: Tool


def claims_from_context(ctx: Context, secret: str) -> InternalClaims:
    token = parse_bearer((ctx.headers or {}).get(HEADER))
    if token is None:
        raise ToolError("Нет служебного токена")
    try:
        return decode_internal_token(token, secret=secret)
    except jwt.InvalidTokenError as exc:
        raise ToolError("Недействительный служебный токен") from exc


def authorize(ctx: Context, secret: str, tool: Tool, client_id: str) -> Authorized:
    """Re-check RBAC + ABAC for the end user, independently of the gateway."""
    claims = claims_from_context(ctx, secret)
    if not can_use_tool(claims.role, tool.value):
        log.warning(
            "mcp_tool_denied", tool=tool.value, role=claims.role.value, user=str(claims.user_id)
        )
        raise ToolError(f"Недостаточно прав для инструмента {tool.value}")
    try:
        client_uuid = uuid.UUID(client_id)
    except ValueError as exc:
        raise ToolError("Некорректный client_id") from exc
    if not claims.can_access_client(client_uuid):
        log.warning(
            "mcp_client_denied", tool=tool.value, client_id=client_id, user=str(claims.user_id)
        )
        raise ToolError("Клиент недоступен")
    return Authorized(claims=claims, client_id=client_uuid, tool=tool)
