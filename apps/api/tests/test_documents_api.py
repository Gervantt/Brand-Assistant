import uuid

import httpx

from brand_shared.permissions import Role
from tests.conftest import RoleHeaders


async def bean_there_id(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    clients = (await client.get("/clients", headers=headers)).json()
    return str(next(c["id"] for c in clients if c["slug"] == "bean-there"))


async def test_upload_list_and_delete(client: httpx.AsyncClient, as_role: RoleHeaders) -> None:
    copywriter = await as_role(Role.COPYWRITER)
    client_id = await bean_there_id(client, copywriter)
    marker = uuid.uuid4().hex[:8]
    content = f"# Памятка {marker}\n\n## Wi-Fi\n\nПароль от гостевого Wi-Fi: coffee-{marker}.\n"

    uploaded = await client.post(
        f"/clients/{client_id}/documents",
        files={"file": (f"wifi-{marker}.md", content.encode(), "text/markdown")},
        headers=copywriter,
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["chunks"] == 1
    assert body["duplicate"] is False

    listed = (await client.get(f"/clients/{client_id}/documents", headers=copywriter)).json()
    assert any(d["title"] == f"Памятка {marker}" for d in listed)

    deleted = await client.delete(
        f"/clients/{client_id}/documents/{body['document_id']}", headers=copywriter
    )
    assert deleted.status_code == 204
    again = await client.delete(
        f"/clients/{client_id}/documents/{body['document_id']}", headers=copywriter
    )
    assert again.status_code == 404


async def test_viewer_cannot_upload(client: httpx.AsyncClient, as_role: RoleHeaders) -> None:
    viewer = await as_role(Role.VIEWER)
    client_id = await bean_there_id(client, viewer)
    response = await client.post(
        f"/clients/{client_id}/documents",
        files={"file": ("x.md", b"# X\n\ntext", "text/markdown")},
        headers=viewer,
    )
    assert response.status_code == 403
    # Listing is fine for anyone with access to the client.
    assert (await client.get(f"/clients/{client_id}/documents", headers=viewer)).status_code == 200


async def test_upload_rejects_bad_files(client: httpx.AsyncClient, as_role: RoleHeaders) -> None:
    copywriter = await as_role(Role.COPYWRITER)
    client_id = await bean_there_id(client, copywriter)
    image = await client.post(
        f"/clients/{client_id}/documents",
        files={"file": ("logo.png", b"\x89PNG", "image/png")},
        headers=copywriter,
    )
    assert image.status_code == 415
    empty = await client.post(
        f"/clients/{client_id}/documents",
        files={"file": ("empty.md", b"# Only a title\n", "text/markdown")},
        headers=copywriter,
    )
    assert empty.status_code == 400
    assert "текст" in empty.json()["detail"]


async def test_upload_to_foreign_client_is_404(
    client: httpx.AsyncClient, as_role: RoleHeaders
) -> None:
    admin = await as_role(Role.ADMIN)
    peakform = next(
        c["id"]
        for c in (await client.get("/clients", headers=admin)).json()
        if c["slug"] == "peakform"
    )
    copywriter = await as_role(Role.COPYWRITER)
    response = await client.post(
        f"/clients/{peakform}/documents",
        files={"file": ("x.md", b"# X\n\ntext", "text/markdown")},
        headers=copywriter,
    )
    assert response.status_code == 404
