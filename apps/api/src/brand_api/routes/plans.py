import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from brand_api.auth.deps import AccessibleClient, require
from brand_api.auth.principal import Principal
from brand_api.deps import AgentDep, SessionDep
from brand_shared.db.models import ContentPlanRecord
from brand_shared.permissions import Action, Tool

router = APIRouter(tags=["plans"])
Publisher = Annotated[Principal, Depends(require(Action.PUBLISH_PLAN))]


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_id: uuid.UUID
    title: str
    period_start: date
    period_end: date
    payload: dict[str, Any]
    approved_at: datetime


class PublishRequest(BaseModel):
    client_id: uuid.UUID
    draft_id: str = Field(min_length=8, max_length=64)


@router.get("/clients/{client_id}/plans")
async def list_plans(client: AccessibleClient, session: SessionDep) -> list[PlanOut]:
    rows = (
        await session.scalars(
            select(ContentPlanRecord)
            .where(ContentPlanRecord.client_id == client.id)
            .order_by(ContentPlanRecord.approved_at.desc())
        )
    ).all()
    return [PlanOut.model_validate(r) for r in rows]


@router.post("/plans/publish")
async def publish_plan(
    body: PublishRequest, principal: Publisher, agent: AgentDep
) -> dict[str, Any]:
    """The UI's "Опубликовать" button. Goes through the same MCP tool and checks as the agent."""
    if not principal.can_access_client(body.client_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Клиент не найден")
    async with agent.mcp.session(principal, trace_id=uuid.uuid4().hex) as mcp:
        outcome = await mcp.call(
            Tool.PUBLISH_CONTENT_PLAN.value,
            {"client_id": str(body.client_id), "draft_id": body.draft_id},
        )
    if not outcome.ok or outcome.data is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, outcome.text or "Не удалось опубликовать")
    return outcome.data
