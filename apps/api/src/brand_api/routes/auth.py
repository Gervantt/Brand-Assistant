from fastapi import APIRouter, HTTPException, Request, status

from brand_api.auth.deps import CurrentUser
from brand_api.auth.schemas import (
    DemoAccount,
    DemoLoginRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from brand_api.auth.service import (
    EmailTakenError,
    authenticate,
    create_user,
    get_user_by_email,
    issue_token,
)
from brand_api.deps import LoginLimiterDep, SessionDep, SettingsDep
from brand_api.seed import DEMO_USERS
from brand_shared.db.models import User
from brand_shared.permissions import Role

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    """Self-registration creates a viewer with no client access; an admin assigns clients."""
    if not settings.allow_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Регистрация отключена")
    try:
        user = await create_user(
            session,
            email=body.email,
            password=body.password,
            full_name=body.full_name,
            role=Role.VIEWER,
            clients=[],
            bcrypt_rounds=settings.bcrypt_rounds,
        )
    except EmailTakenError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Пользователь с таким email уже существует"
        ) from exc
    await session.commit()
    return issue_token(user, settings)


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    limiter: LoginLimiterDep,
) -> TokenResponse:
    client_ip = request.client.host if request.client else "unknown"
    attempt = await limiter.hit(f"{client_ip}:{body.email}")
    if not attempt.allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Слишком много попыток входа. Попробуйте через 15 минут.",
            headers={"Retry-After": str(attempt.retry_after_s)},
        )
    user = await authenticate(
        session, body.email, body.password, bcrypt_rounds=settings.bcrypt_rounds
    )
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный email или пароль")
    return issue_token(user, settings)


@router.get("/demo-accounts")
async def demo_accounts(settings: SettingsDep) -> list[DemoAccount]:
    if not settings.demo_mode:
        return []
    return [DemoAccount(role=d.role, email=d.email, description=d.description) for d in DEMO_USERS]


@router.post("/demo-login")
async def demo_login(
    body: DemoLoginRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    """One-click demo login: the frontend never needs to know DEMO_PASSWORD."""
    if not settings.demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Демо-режим отключён")
    demo = next(d for d in DEMO_USERS if d.role is body.role)
    user = await get_user_by_email(session, demo.email)
    if user is None or not user.is_demo or not user.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Демо-аккаунт не найден")
    return issue_token(user, settings)


@router.get("/me")
async def me(principal: CurrentUser, session: SessionDep) -> UserOut:
    user = await session.get(User, principal.id)
    return UserOut.model_validate(user)
