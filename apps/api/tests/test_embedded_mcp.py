"""Production topology: the MCP server mounted inside the API process (MCP_MODE=embedded),
reached over real HTTP on localhost — exactly how it runs on Render."""

import asyncio
import os
import socket
from collections.abc import AsyncIterator

import httpx
import pytest
import uvicorn

from brand_api.config import Settings
from brand_api.main import create_app
from brand_shared.permissions import Role
from tests.agent_helpers import chat, new_conversation, tool_reply
from tests.conftest import REPO_ROOT, demo_headers
from tests.fakes import ScriptedProvider, reply


@pytest.fixture
async def embedded_api(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[str, ScriptedProvider]]:
    monkeypatch.setenv("MODEL_CACHE_DIR", str(REPO_ROOT / ".cache" / "fastembed"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    llm = ScriptedProvider(
        "groq",
        [
            tool_reply(("search_brandbook", {"query": "фирменные цвета"})),
            reply("Основной цвет — #4B2E2A [1]."),
        ],
    )
    app = create_app(
        settings.model_copy(update={"mcp_mode": "embedded", "port": port}),
        llm_providers={"groq": llm},
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 - uvicorn exposes only a bool flag
        await asyncio.sleep(0.02)
    yield f"http://127.0.0.1:{port}", llm
    server.should_exit = True
    await task


async def test_agent_uses_the_embedded_mcp_server(
    embedded_api: tuple[str, ScriptedProvider],
) -> None:
    base_url, llm = embedded_api
    async with httpx.AsyncClient(base_url=base_url, timeout=60) as client:
        headers = await demo_headers(client, Role.VIEWER)
        conversation_id = await new_conversation(client, headers)
        events = await chat(client, headers, conversation_id, "Какие фирменные цвета?")

        # The internal endpoint is not reachable without the service token.
        unauthenticated = await client.post("/mcp-internal/mcp", json={"jsonrpc": "2.0"})

    end = next(data for name, data in events if name == "tool_end")
    assert end["ok"] is True
    assert any(name == "citations" for name, _ in events)
    assert "#4B2E2A" in llm.requests[1][1].messages[-1].content
    assert unauthenticated.status_code == 401
    assert os.environ["MODEL_CACHE_DIR"]
