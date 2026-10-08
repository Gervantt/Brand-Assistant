"""End-to-end smoke test against a running stack (docker compose up).

    uv run python scripts/smoke.py [--api http://localhost:8000] [--web http://localhost:5173]

Checks health, demo login for every role, RBAC/ABAC, ingested documents, the SSE agent stream
(with or without an LLM key) and the admin metrics. Exits non-zero on the first failure.
"""

import argparse
import json
import sys
import time

import httpx

ROLES = ["viewer", "copywriter", "manager", "admin"]


def check(condition: bool, message: str) -> None:
    print(("  ok   " if condition else "  FAIL ") + message)
    if not condition:
        sys.exit(1)


def wait_healthy(client: httpx.Client, timeout_s: float = 180) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if client.get("/health").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(3)
    check(False, "API became healthy")


def sse_events(text: str) -> list[tuple[str, dict[str, object]]]:
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        name = next((ln[6:].strip() for ln in block.splitlines() if ln.startswith("event:")), "")
        data = "".join(ln[5:].strip() for ln in block.splitlines() if ln.startswith("data:"))
        if name and data:
            events.append((name, json.loads(data)))
    return events


Headers = dict[str, dict[str, str]]


def check_auth(api: httpx.Client) -> Headers:
    print("auth")
    accounts = api.get("/auth/demo-accounts").json()
    check([a["role"] for a in accounts] == ROLES, "four demo accounts listed")
    headers: Headers = {}
    for role in ROLES:
        response = api.post("/auth/demo-login", json={"role": role})
        check(response.status_code == 200, f"demo login as {role}")
        headers[role] = {"Authorization": f"Bearer {response.json()['access_token']}"}
    return headers


def check_access(api: httpx.Client, auth: Headers) -> str:
    print("rbac / abac")
    viewer_clients = api.get("/clients", headers=auth["viewer"]).json()
    check([c["slug"] for c in viewer_clients] == ["bean-there"], "viewer sees only Bean There")
    check(api.get("/admin/users", headers=auth["viewer"]).status_code == 403, "viewer: admin 403")
    return str(viewer_clients[0]["id"])


def check_rag(api: httpx.Client, auth: Headers, client_id: str) -> None:
    print("rag")
    deadline = time.monotonic() + 300  # first start downloads the embedding model
    documents: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        documents = api.get(f"/clients/{client_id}/documents", headers=auth["viewer"]).json()
        if len(documents) >= 2:
            break
        time.sleep(5)
    check(len(documents) >= 2, f"brand book and brief ingested ({len(documents)} documents)")


def check_agent(api: httpx.Client, auth: Headers, client_id: str) -> None:
    print("agent (SSE)")
    conversation = api.post(
        "/conversations", json={"client_id": client_id}, headers=auth["viewer"]
    ).json()
    response = api.post(
        f"/conversations/{conversation['id']}/messages",
        json={"content": "Какие фирменные цвета у бренда?"},
        headers=auth["viewer"],
    )
    events = sse_events(response.text)
    names = [name for name, _ in events]
    check(names[:1] == ["meta"], "stream starts with meta")
    if "done" in names:
        tools = [data.get("name") for name, data in events if name == "tool_start"]
        check(True, f"agent answered (tools: {tools})")
    else:
        error = next((data for name, data in events if name == "error"), {})
        check(error.get("code") == "llm_unavailable", f"no usable LLM -> clear error: {error}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--web", default="http://localhost:5173")
    args = parser.parse_args()
    api = httpx.Client(base_url=args.api, timeout=120)

    print("health")
    wait_healthy(api)
    body = api.get("/health").json()
    check(body["checks"] == {"database": "ok", "redis": "ok"}, f"database and redis: {body}")

    auth = check_auth(api)
    client_id = check_access(api, auth)
    check_rag(api, auth, client_id)
    check_agent(api, auth, client_id)

    print("metrics")
    check(api.get("/admin/metrics", headers=auth["admin"]).status_code == 200, "admin metrics")

    print("web")
    try:
        page = httpx.get(args.web, timeout=10)
        check(page.status_code == 200 and "Brand Assistant" in page.text, "web app served")
    except httpx.HTTPError as exc:
        check(False, f"web app reachable ({exc})")
    print("smoke test passed")


if __name__ == "__main__":
    main()
