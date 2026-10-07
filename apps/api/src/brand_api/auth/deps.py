import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from brand_api.auth.principal import Principal
from brand_api.auth.security import decode_access_token
from brand_api.deps import SessionDep, SettingsDep
from brand_shared.db.models import Client, User
from brand_shared.permissions import Action

_bearer = HTTPBearer(auto_error=False, description="JWT from /auth/login")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def current_principal(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise _unauthorized("Требуется авторизация")
    try:
        user_id = decode_access_token(
            credentials.credentials, secret=settings.jwt_secret.get_secret_value()
        )
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("Сессия истекла, войдите снова") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("Недействительный токен") from exc

    # Role and client assignments are read from the DB on every request, so revoking access
    # takes effect immediately instead of waiting for the token to expire.
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("Пользователь не найден или отключён")
    return Principal.from_user(user)


CurrentUser = Annotated[Principal, Depends(current_principal)]


def require(action: Action) -> Callable[[Principal], Awaitable[Principal]]:
    async def dependency(principal: CurrentUser) -> Principal:
        if not principal.can(action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав для этого действия",
            )
        return principal

    return dependency


async def accessible_client(
    client_id: uuid.UUID, principal: CurrentUser, session: SessionDep
) -> Client:
    """ABAC guard. Inaccessible and non-existent clients both return 404 (no enumeration)."""
    client = await session.get(Client, client_id)
    if client is None or not principal.can_access_client(client.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Клиент не найден")
    return client


AccessibleClient = Annotated[Client, Depends(accessible_client)]
