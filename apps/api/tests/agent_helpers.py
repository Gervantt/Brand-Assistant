"""Helpers for agent tests: an API client with scripted LLM providers and an SSE reader."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from asgi_lifespan import LifespanManager

from brand_api.config import Settings
from brand_api.llm.types import ChatResponse, ToolCall, Usage
from brand_api.main import create_app
from tests.fakes import ScriptedProvider

Event = tuple[str, dict[str, Any]]


def tool_reply(*calls: tuple[str, dict[str, Any]], text: str = "") -> ChatResponse:
    return ChatResponse(
        content=text,
        tool_calls=[
            ToolCall(id=f"call_{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ],
        usage=Usage(input_tokens=50, output_tokens=10),
        finish_reason="tool_calls",
    )


@asynccontextmanager
async def api_with_llm(
    settings: Settings, provider: ScriptedProvider
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, llm_providers={provider.name: provider})
    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=60
        ) as client,
    ):
        yield client


def parse_sse(raw: str) -> list[Event]:
    events: list[Event] = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        name, data = "message", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if data:
            events.append((name, json.loads(data)))
    return events


async def chat(
    client: httpx.AsyncClient, headers: dict[str, str], conversation_id: str, text: str
) -> list[Event]:
    response = await client.post(
        f"/conversations/{conversation_id}/messages", json={"content": text}, headers=headers
    )
    assert response.status_code == 200, response.text
    return parse_sse(response.text)


async def new_conversation(
    client: httpx.AsyncClient, headers: dict[str, str], slug: str = "bean-there"
) -> str:
    clients = (await client.get("/clients", headers=headers)).json()
    client_id = next(c["id"] for c in clients if c["slug"] == slug)
    created = await client.post("/conversations", json={"client_id": client_id}, headers=headers)
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def names(events: list[Event]) -> list[str]:
    return [name for name, _ in events]


def text_of(events: list[Event]) -> str:
    return "".join(data["text"] for name, data in events if name == "token")
