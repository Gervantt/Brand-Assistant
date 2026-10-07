import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager

from brand_api.auth.security import create_access_token
from brand_api.config import Settings
from brand_api.main import create_app
from brand_shared.permissions import Role
from tests.conftest import DEMO_PASSWORD, demo_headers


def unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:10]}@example.com"


async def test_register_creates_viewer_without_clients(client: httpx.AsyncClient) -> None:
    email = unique_email()
    response = await client.post(
        "/auth/register", json={"email": email.upper(), "password": "s3cret-pass", "full_name": "Q"}
    )
    assert response.status_code == 201
    user = response.json()["user"]
    assert user["email"] == email  # normalised to lower case
    assert user["role"] == "viewer"
    assert user["clients"] == []


async def test_register_rejects_duplicates_and_weak_passwords(client: httpx.AsyncClient) -> None:
    email = unique_email()
    assert (
        await client.post("/auth/register", json={"email": email, "password": "s3cret-pass"})
    ).status_code == 201
    duplicate = await client.post(
        "/auth/register", json={"email": email, "password": "s3cret-pass"}
    )
    assert duplicate.status_code == 409
    weak = await client.post("/auth/register", json={"email": unique_email(), "password": "short"})
    assert weak.status_code == 422
    huge = await client.post("/auth/register", json={"email": unique_email(), "password": "я" * 40})
    assert huge.status_code == 422  # 80 bytes > bcrypt limit


async def test_login_and_me(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/auth/login", json={"email": "Manager@Demo.com", "password": DEMO_PASSWORD}
    )
    assert response.status_code == 200
    token = response.json()["access_token"]

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["role"] == "manager"
    assert {c["slug"] for c in body["clients"]} == {"bean-there", "peakform"}


@pytest.mark.parametrize(
    ("email", "password"),
    [("manager@demo.com", "wrong-password"), ("nobody@demo.com", DEMO_PASSWORD)],
)
async def test_bad_credentials_get_the_same_answer(
    client: httpx.AsyncClient, email: str, password: str
) -> None:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 401
    assert response.json()["detail"] == "Неверный email или пароль"


@pytest.mark.parametrize("header", [None, "Bearer garbage", "Basic abc"])
async def test_me_requires_valid_token(client: httpx.AsyncClient, header: str | None) -> None:
    headers = {"Authorization": header} if header else {}
    response = await client.get("/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_token_for_deleted_user_is_rejected(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    token, _ = create_access_token(
        uuid.uuid4(), secret=settings.jwt_secret.get_secret_value(), ttl_minutes=5
    )
    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


@pytest.mark.parametrize("role", list(Role))
async def test_demo_login_for_each_role(client: httpx.AsyncClient, role: Role) -> None:
    headers = await demo_headers(client, role)
    me = await client.get("/auth/me", headers=headers)
    assert me.json()["role"] == role.value
    assert me.json()["is_demo"] is True


async def test_demo_accounts_listing_has_no_passwords(client: httpx.AsyncClient) -> None:
    accounts = (await client.get("/auth/demo-accounts")).json()
    assert [a["email"] for a in accounts] == [
        "viewer@demo.com",
        "copywriter@demo.com",
        "manager@demo.com",
        "admin@demo.com",
    ]
    assert DEMO_PASSWORD not in str(accounts)


@pytest.fixture
async def locked_down_client(settings: Settings, seeded: None) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings.model_copy(update={"demo_mode": False, "allow_registration": False}))
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http,
    ):
        yield http


async def test_demo_login_and_registration_can_be_disabled(
    locked_down_client: httpx.AsyncClient,
) -> None:
    demo = await locked_down_client.post("/auth/demo-login", json={"role": "admin"})
    assert demo.status_code == 404
    assert (await locked_down_client.get("/auth/demo-accounts")).json() == []
    register = await locked_down_client.post(
        "/auth/register", json={"email": unique_email(), "password": "s3cret-pass"}
    )
    assert register.status_code == 403
