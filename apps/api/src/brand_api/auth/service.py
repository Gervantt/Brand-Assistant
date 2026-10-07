import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brand_api.auth.schemas import TokenResponse, UserOut
from brand_api.auth.security import (
    burn_password_check,
    create_access_token,
    hash_password,
    verify_password,
)
from brand_api.config import Settings
from brand_shared.db.models import Client, User
from brand_shared.permissions import Role


class EmailTakenError(Exception):
    pass


class UnknownClientError(Exception):
    def __init__(self, missing: Iterable[uuid.UUID]) -> None:
        super().__init__(", ".join(str(m) for m in missing))


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.scalars(select(User).where(User.email == email.strip().lower()))
    return result.first()


async def authenticate(
    session: AsyncSession, email: str, password: str, *, bcrypt_rounds: int
) -> User | None:
    user = await get_user_by_email(session, email)
    if user is None:
        burn_password_check(password, bcrypt_rounds)
        return None
    if not verify_password(password, user.password_hash) or not user.is_active:
        return None
    return user


async def load_clients(session: AsyncSession, client_ids: Iterable[uuid.UUID]) -> list[Client]:
    wanted = set(client_ids)
    if not wanted:
        return []
    clients = list((await session.scalars(select(Client).where(Client.id.in_(wanted)))).all())
    missing = wanted - {c.id for c in clients}
    if missing:
        raise UnknownClientError(missing)
    return clients


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    role: Role,
    clients: list[Client],
    bcrypt_rounds: int,
) -> User:
    if await get_user_by_email(session, email) is not None:
        raise EmailTakenError(email)
    user = User(
        email=email.strip().lower(),
        password_hash=hash_password(password, bcrypt_rounds),
        full_name=full_name,
        role=role.value,
        clients=clients,
    )
    session.add(user)
    await session.flush()
    return user


def issue_token(user: User, settings: Settings) -> TokenResponse:
    token, expires_at = create_access_token(
        user.id,
        secret=settings.jwt_secret.get_secret_value(),
        ttl_minutes=settings.jwt_ttl_minutes,
    )
    return TokenResponse(
        access_token=token, expires_at=expires_at, user=UserOut.model_validate(user)
    )
