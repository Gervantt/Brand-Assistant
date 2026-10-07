import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from brand_api.auth.deps import require
from brand_api.auth.principal import Principal
from brand_api.auth.schemas import (
    AdminUserCreate,
    AdminUserUpdate,
    ClientCreate,
    ClientOut,
    ClientUpdate,
    UserOut,
)
from brand_api.auth.service import EmailTakenError, UnknownClientError, create_user, load_clients
from brand_api.deps import SessionDep, SettingsDep
from brand_api.observability.metrics import MetricsReport, build_report
from brand_shared.db.models import Client, User
from brand_shared.permissions import Action

router = APIRouter(prefix="/admin", tags=["admin"])

UserAdmin = Annotated[Principal, Depends(require(Action.MANAGE_USERS))]
MetricsViewer = Annotated[Principal, Depends(require(Action.VIEW_METRICS))]
ClientAdmin = Annotated[Principal, Depends(require(Action.MANAGE_CLIENTS))]


def _unknown_clients(exc: UnknownClientError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Неизвестные клиенты: {exc}")


# ---- users -------------------------------------------------------------------------------


@router.get("/users")
async def list_users(_: UserAdmin, session: SessionDep) -> list[UserOut]:
    users = (await session.scalars(select(User).order_by(User.created_at))).all()
    return [UserOut.model_validate(u) for u in users]


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user_admin(
    body: AdminUserCreate, _: UserAdmin, session: SessionDep, settings: SettingsDep
) -> UserOut:
    try:
        clients = await load_clients(session, body.client_ids)
        user = await create_user(
            session,
            email=body.email,
            password=body.password,
            full_name=body.full_name,
            role=body.role,
            clients=clients,
            bcrypt_rounds=settings.bcrypt_rounds,
        )
    except UnknownClientError as exc:
        raise _unknown_clients(exc) from exc
    except EmailTakenError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email уже занят") from exc
    await session.commit()
    return UserOut.model_validate(user)


@router.patch("/users/{user_id}")
async def update_user(
    user_id: uuid.UUID, body: AdminUserUpdate, admin: UserAdmin, session: SessionDep
) -> UserOut:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    if user.is_demo:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Демо-аккаунты нельзя изменять")
    if user.id == admin.id and (body.role is not None or body.is_active is False):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Нельзя изменить свою роль или отключить себя"
        )
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.role is not None:
        user.role = body.role.value
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.client_ids is not None:
        try:
            user.clients = await load_clients(session, body.client_ids)
        except UnknownClientError as exc:
            raise _unknown_clients(exc) from exc
    await session.commit()
    return UserOut.model_validate(user)


# ---- clients -----------------------------------------------------------------------------


@router.post("/clients", status_code=status.HTTP_201_CREATED)
async def create_client(body: ClientCreate, _: ClientAdmin, session: SessionDep) -> ClientOut:
    if (await session.scalars(select(Client).where(Client.slug == body.slug))).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Клиент с таким slug уже существует")
    client = Client(slug=body.slug, name=body.name, profile=body.profile.model_dump())
    session.add(client)
    await session.commit()
    return ClientOut.model_validate(client)


@router.patch("/clients/{client_id}")
async def update_client(
    client_id: uuid.UUID, body: ClientUpdate, _: ClientAdmin, session: SessionDep
) -> ClientOut:
    client = await session.get(Client, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Клиент не найден")
    if body.name is not None:
        client.name = body.name
    if body.profile is not None:
        client.profile = body.profile.model_dump()
    await session.commit()
    return ClientOut.model_validate(client)


# ---- metrics -----------------------------------------------------------------------------


@router.get("/metrics")
async def metrics(
    _: MetricsViewer,
    session: SessionDep,
    hours: Annotated[int, Query(ge=1, le=24 * 90)] = 24,
) -> MetricsReport:
    """LLM latency (avg/p95), cost per day and per model, error and fallback rates, cache
    savings — all from `llm_calls`."""
    return await build_report(session, hours)
