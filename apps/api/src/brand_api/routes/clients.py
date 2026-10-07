from fastapi import APIRouter
from sqlalchemy import select

from brand_api.auth.deps import AccessibleClient, CurrentUser
from brand_api.auth.schemas import ClientOut, ClientSummary
from brand_api.deps import SessionDep
from brand_shared.db.models import Client
from brand_shared.permissions import Role

router = APIRouter(prefix="/clients", tags=["clients"])


@router.get("")
async def list_clients(principal: CurrentUser, session: SessionDep) -> list[ClientSummary]:
    """Only clients the caller is assigned to (ABAC); admins see every client."""
    query = select(Client).order_by(Client.name)
    if principal.role is not Role.ADMIN:
        query = query.where(Client.id.in_(principal.client_ids))
    return [ClientSummary.model_validate(c) for c in (await session.scalars(query)).all()]


@router.get("/{client_id}")
async def get_client(client: AccessibleClient) -> ClientOut:
    return ClientOut.model_validate(client)
