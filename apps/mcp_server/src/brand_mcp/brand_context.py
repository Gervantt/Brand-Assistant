import uuid
from dataclasses import dataclass

from mcp.server.mcpserver.exceptions import ToolError

from brand_mcp.deps import Deps
from brand_shared.db.models import Client
from brand_shared.logging_setup import get_logger
from brand_shared.schemas.clients import ClientProfile
from brand_shared.schemas.tools import BrandbookHit

log = get_logger("brand_mcp.brand_context")


@dataclass(frozen=True)
class BrandContext:
    name: str
    profile: ClientProfile
    hits: list[BrandbookHit]

    def render(self) -> str:
        p = self.profile
        lines = [
            f"Бренд: {self.name}",
            f"Отрасль: {p.industry}",
            f"Описание: {p.description}",
            f"Целевая аудитория: {p.audience}",
            f"Тон голоса: {p.tone_of_voice}",
            f"Площадки: {', '.join(p.platforms)}",
            f"Рубрики: {', '.join(p.content_pillars)}",
            f"Запрещённые темы: {', '.join(p.banned_topics)}",
            f"Фирменные хэштеги: {' '.join(p.hashtags)}",
        ]
        if self.hits:
            lines.append("\nФрагменты брендбука:")
            lines.extend(
                f"[{i}] ({h.document}{' / ' + h.section if h.section else ''}) {h.text}"
                for i, h in enumerate(self.hits, start=1)
            )
        return "\n".join(lines)


async def load_brand_context(
    deps: Deps, client_id: uuid.UUID, query: str, top_k: int = 6
) -> BrandContext:
    async with deps.sessionmaker() as session:
        client = await session.get(Client, client_id)
        if client is None:
            raise ToolError("Клиент не найден")
    try:
        async with deps.sessionmaker() as session:
            hits = await deps.retriever.search(session, client_id, query, top_k)
    except Exception as exc:  # embeddings API down / quota: generate from the profile alone
        log.warning("brand_context_retrieval_failed", client_id=str(client_id), error=repr(exc))
        hits = []
    return BrandContext(
        name=client.name, profile=ClientProfile.model_validate(client.profile), hits=hits
    )
