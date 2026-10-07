from datetime import UTC, datetime
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.resolve import Resolve
from mcp.types import ToolAnnotations
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from brand_mcp.auth import Authorized, authorize
from brand_mcp.deps import Deps
from brand_mcp.drafts import load_draft
from brand_mcp.tool_log import tool_call
from brand_mcp.tools.common import ClientId
from brand_shared.db.models import ContentPlanRecord
from brand_shared.permissions import Tool
from brand_shared.schemas.tools import PublishResult

# The only tool with a side effect. Idempotent: publishing the same draft twice is a no-op.
PUBLISHES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True)


def _result(record: ContentPlanRecord, *, already: bool) -> PublishResult:
    return PublishResult(
        plan_id=record.id,
        title=record.title,
        items=len(record.payload.get("items", [])),
        approved_at=record.approved_at,
        already_published=already,
    )


def register(server: MCPServer, deps: Deps) -> None:
    secret = deps.settings.mcp_internal_secret.get_secret_value()

    def publish_auth(ctx: Context, client_id: str) -> Authorized:
        return authorize(ctx, secret, Tool.PUBLISH_CONTENT_PLAN, client_id)

    @server.tool(name=Tool.PUBLISH_CONTENT_PLAN.value, annotations=PUBLISHES)
    async def publish_content_plan(
        client_id: ClientId,
        draft_id: Annotated[
            str, Field(min_length=8, max_length=64, description="draft_id из create_content_plan")
        ],
        auth: Annotated[Authorized, Resolve(publish_auth)],
    ) -> PublishResult:
        """Утверждает (публикует) черновик контент-плана. Вызывай только по явной просьбе
        пользователя опубликовать или утвердить план."""
        async with tool_call(auth, draft_id=draft_id):
            async with deps.sessionmaker() as session:
                existing = (
                    await session.scalars(
                        select(ContentPlanRecord).where(
                            ContentPlanRecord.source_draft_id == draft_id
                        )
                    )
                ).first()
                if existing is not None:
                    if existing.client_id != auth.client_id:
                        raise ToolError("Черновик принадлежит другому клиенту")
                    return _result(existing, already=True)

            draft = await load_draft(deps.redis, draft_id)
            if draft is None:
                raise ToolError("Черновик не найден или истёк. Сгенерируйте план заново.")
            if draft.client_id != auth.client_id:
                raise ToolError("Черновик принадлежит другому клиенту")

            record = ContentPlanRecord(
                client_id=draft.client_id,
                source_draft_id=draft_id,
                title=draft.plan.title,
                period_start=draft.plan.period_start,
                period_end=draft.plan.period_end,
                payload=draft.plan.model_dump(mode="json"),
                status="approved",
                created_by=draft.created_by,
                approved_by=auth.claims.user_id,
                approved_at=datetime.now(UTC),
            )
            async with deps.sessionmaker() as session:
                session.add(record)
                try:
                    await session.commit()
                except IntegrityError:  # concurrent publish of the same draft
                    await session.rollback()
                    record = (
                        await session.scalars(
                            select(ContentPlanRecord).where(
                                ContentPlanRecord.source_draft_id == draft_id
                            )
                        )
                    ).one()
                    return _result(record, already=True)
            return _result(record, already=False)
