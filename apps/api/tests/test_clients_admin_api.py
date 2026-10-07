"""ABAC on clients and admin-only management endpoints, checked for every role."""

import uuid

import httpx
import pytest

from brand_shared.permissions import Role
from tests.conftest import RoleHeaders

VISIBLE_SLUGS: dict[Role, set[str]] = {
    Role.VIEWER: {"bean-there"},
    Role.COPYWRITER: {"bean-there"},
    Role.MANAGER: {"bean-there", "peakform"},
    Role.ADMIN: {"bean-there", "peakform"},  # admins see every client
}


async def client_ids(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, str]:
    admin_view = await client.get("/clients", headers=headers)
    return {c["slug"]: c["id"] for c in admin_view.json()}


@pytest.mark.parametrize("role", list(Role))
async def test_client_list_is_filtered_by_assignment(
    client: httpx.AsyncClient, as_role: RoleHeaders, role: Role
) -> None:
    response = await client.get("/clients", headers=await as_role(role))
    slugs = {c["slug"] for c in response.json()}
    assert VISIBLE_SLUGS[role] <= slugs
    if role is not Role.ADMIN:
        assert slugs == VISIBLE_SLUGS[role]


@pytest.mark.parametrize("role", list(Role))
async def test_client_detail_respects_abac(
    client: httpx.AsyncClient, as_role: RoleHeaders, role: Role
) -> None:
    ids = await client_ids(client, await as_role(Role.ADMIN))
    headers = await as_role(role)
    for slug, client_id in ids.items():
        if slug not in {"bean-there", "peakform"}:
            continue
        response = await client.get(f"/clients/{client_id}", headers=headers)
        if slug in VISIBLE_SLUGS[role]:
            assert response.status_code == 200
            assert response.json()["profile"]["platforms"]
        else:
            assert response.status_code == 404  # indistinguishable from "does not exist"


async def test_unknown_client_is_404(client: httpx.AsyncClient, as_role: RoleHeaders) -> None:
    response = await client.get(f"/clients/{uuid.uuid4()}", headers=await as_role(Role.ADMIN))
    assert response.status_code == 404


ADMIN_ENDPOINTS = [
    ("GET", "/admin/users", None),
    ("POST", "/admin/users", {"email": "x@example.com", "password": "s3cret-pass"}),
    ("POST", "/admin/clients", {"slug": "new-client", "name": "New"}),
]


@pytest.mark.parametrize("role", [Role.VIEWER, Role.COPYWRITER, Role.MANAGER])
@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ENDPOINTS)
async def test_admin_endpoints_forbidden_for_non_admins(
    client: httpx.AsyncClient,
    as_role: RoleHeaders,
    role: Role,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    response = await client.request(method, path, json=body, headers=await as_role(role))
    assert response.status_code == 403


async def test_admin_creates_client_and_assigns_user(
    client: httpx.AsyncClient, as_role: RoleHeaders
) -> None:
    admin = await as_role(Role.ADMIN)
    slug = f"client-{uuid.uuid4().hex[:8]}"
    created = await client.post(
        "/admin/clients",
        json={"slug": slug, "name": "Новый клиент", "profile": {"platforms": ["Instagram"]}},
        headers=admin,
    )
    assert created.status_code == 201
    client_id = created.json()["id"]

    email = f"cw-{uuid.uuid4().hex[:8]}@example.com"
    user = await client.post(
        "/admin/users",
        json={
            "email": email,
            "password": "s3cret-pass",
            "role": "copywriter",
            "client_ids": [client_id],
        },
        headers=admin,
    )
    assert user.status_code == 201
    assert user.json()["clients"][0]["slug"] == slug

    login = await client.post("/auth/login", json={"email": email, "password": "s3cret-pass"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert [c["slug"] for c in (await client.get("/clients", headers=headers)).json()] == [slug]

    # Revoking the assignment takes effect immediately for the same token.
    user_id = user.json()["id"]
    revoked = await client.patch(f"/admin/users/{user_id}", json={"client_ids": []}, headers=admin)
    assert revoked.status_code == 200
    assert (await client.get(f"/clients/{client_id}", headers=headers)).status_code == 404

    deactivated = await client.patch(
        f"/admin/users/{user_id}", json={"is_active": False}, headers=admin
    )
    assert deactivated.status_code == 200
    assert (await client.get("/auth/me", headers=headers)).status_code == 401


async def test_admin_input_validation(client: httpx.AsyncClient, as_role: RoleHeaders) -> None:
    admin = await as_role(Role.ADMIN)
    bad_client = await client.post(
        "/admin/users",
        json={
            "email": "v@example.com",
            "password": "s3cret-pass",
            "client_ids": [str(uuid.uuid4())],
        },
        headers=admin,
    )
    assert bad_client.status_code == 422
    bad_role = await client.post(
        "/admin/users",
        json={"email": "w@example.com", "password": "s3cret-pass", "role": "superuser"},
        headers=admin,
    )
    assert bad_role.status_code == 422
    bad_slug = await client.post(
        "/admin/clients", json={"slug": "Bad Slug!", "name": "x"}, headers=admin
    )
    assert bad_slug.status_code == 422
    duplicate = await client.post(
        "/admin/clients", json={"slug": "bean-there", "name": "x"}, headers=admin
    )
    assert duplicate.status_code == 409


async def test_demo_accounts_are_protected(client: httpx.AsyncClient, as_role: RoleHeaders) -> None:
    admin = await as_role(Role.ADMIN)
    users = (await client.get("/admin/users", headers=admin)).json()
    viewer = next(u for u in users if u["email"] == "viewer@demo.com")
    response = await client.patch(
        f"/admin/users/{viewer['id']}", json={"role": "admin"}, headers=admin
    )
    assert response.status_code == 403
