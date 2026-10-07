import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from brand_api.auth.security import BCRYPT_MAX_BYTES
from brand_shared.permissions import Role
from brand_shared.schemas.clients import ClientProfile

MIN_PASSWORD_LENGTH = 8


def _check_password(value: str) -> str:
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов")
    if len(value.encode()) > BCRYPT_MAX_BYTES:
        raise ValueError(f"пароль должен быть не длиннее {BCRYPT_MAX_BYTES} байт")
    return value


def _normalize_email(value: str) -> str:
    return value.strip().lower()


Password = Annotated[str, AfterValidator(_check_password)]
Email = Annotated[EmailStr, AfterValidator(_normalize_email)]


class RegisterRequest(BaseModel):
    email: Email
    password: Password
    full_name: str = Field(default="", max_length=200)


class LoginRequest(BaseModel):
    email: Email
    password: str = Field(max_length=256)


class DemoLoginRequest(BaseModel):
    role: Role


class ClientSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str


class ClientOut(ClientSummary):
    profile: ClientProfile


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    is_demo: bool
    clients: list[ClientSummary]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_at: datetime
    user: UserOut


class DemoAccount(BaseModel):
    role: Role
    email: str
    description: str


# ---- admin -------------------------------------------------------------------------------

Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")]


class AdminUserCreate(BaseModel):
    email: Email
    password: Password
    full_name: str = Field(default="", max_length=200)
    role: Role = Role.VIEWER
    client_ids: list[uuid.UUID] = Field(default_factory=list)


class AdminUserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    role: Role | None = None
    is_active: bool | None = None
    client_ids: list[uuid.UUID] | None = None


class ClientCreate(BaseModel):
    slug: Slug
    name: str = Field(min_length=1, max_length=200)
    profile: ClientProfile = Field(default_factory=ClientProfile)


class ClientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    profile: ClientProfile | None = None
