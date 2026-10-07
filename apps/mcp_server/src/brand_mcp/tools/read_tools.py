from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.resolve import Resolve
from mcp.types import ToolAnnotations
from pydantic import Field

from brand_mcp.auth import Authorized, authorize
from brand_mcp.deps import Deps
from brand_mcp.tool_log import tool_call
from brand_mcp.tools.common import ClientId
from brand_shared.db.models import Client
from brand_shared.permissions import Tool
from brand_shared.schemas.clients import ClientProfile
from brand_shared.schemas.tools import ClientProfileResult, SearchBrandbookResult

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)


def register(server: MCPServer, deps: Deps) -> None:
    secret = deps.settings.mcp_internal_secret.get_secret_value()

    def auth_profile(ctx: Context, client_id: str) -> Authorized:
        return authorize(ctx, secret, Tool.GET_CLIENT_PROFILE, client_id)

    def auth_search(ctx: Context, client_id: str) -> Authorized:
        return authorize(ctx, secret, Tool.SEARCH_BRANDBOOK, client_id)

    @server.tool(name=Tool.GET_CLIENT_PROFILE.value, annotations=READ_ONLY)
    async def get_client_profile(
        client_id: ClientId, auth: Annotated[Authorized, Resolve(auth_profile)]
    ) -> ClientProfileResult:
        """Профиль клиента: отрасль, целевая аудитория, тон голоса, площадки, рубрики,
        запрещённые темы и фирменные хэштеги. Используй перед генерацией контента."""
        async with tool_call(auth), deps.sessionmaker() as session:
            client = await session.get(Client, auth.client_id)
            if client is None:
                raise ToolError("Клиент не найден")
            return ClientProfileResult(
                client_id=client.id,
                name=client.name,
                profile=ClientProfile.model_validate(client.profile),
            )

    @server.tool(name=Tool.SEARCH_BRANDBOOK.value, annotations=READ_ONLY)
    async def search_brandbook(
        client_id: ClientId,
        query: Annotated[str, Field(min_length=2, max_length=500, description="Вопрос или тема")],
        auth: Annotated[Authorized, Resolve(auth_search)],
        top_k: Annotated[int, Field(ge=1, le=10)] = 5,
    ) -> SearchBrandbookResult:
        """Поиск по загруженному брендбуку клиента. Возвращает релевантные фрагменты с
        источниками и уверенность. Если found=false — в брендбуке ответа нет, не выдумывай."""
        async with tool_call(auth, query=query[:100]), deps.sessionmaker() as session:
            hits = await deps.retriever.search(session, auth.client_id, query, top_k)
        confidence = max((h.score for h in hits), default=0.0)
        return SearchBrandbookResult(
            query=query,
            hits=hits,
            confidence=round(confidence, 3),
            found=confidence >= deps.retriever.threshold,
        )
