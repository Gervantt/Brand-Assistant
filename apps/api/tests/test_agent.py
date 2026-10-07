"""Agent integration: real API + real MCP server + real Postgres/Redis; scripted LLM only."""

import json
import uuid

import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from brand_api.config import Settings
from brand_api.db import create_engine, create_sessionmaker
from brand_shared.db.models import ContentPlanRecord
from brand_shared.permissions import Role
from tests.agent_helpers import (
    api_with_llm,
    chat,
    names,
    new_conversation,
    parse_sse,
    text_of,
    tool_reply,
)
from tests.conftest import TEST_REDIS_URL, demo_headers
from tests.fakes import ScriptedProvider, error, reply

PLAN_JSON = json.dumps(
    {
        "title": "Неделя тёплых напитков",
        "period_start": "2026-11-02",
        "period_end": "2026-11-08",
        "goal": "Вовлечение",
        "items": [
            {
                "date": "2026-11-04",
                "platform": "Instagram",
                "format": "reels",
                "rubric": "Бариста и команда",
                "title": "Как варится наш фильтр",
                "text": "Показываем, как бариста Айгерим заваривает фильтр-кофе на V60.",
                "hashtags": ["#BeanThere"],
                "visual_idea": "Крупный план воронки и струи воды",
            }
        ],
    },
    ensure_ascii=False,
)


async def test_brandbook_question_flow(settings: Settings) -> None:
    llm = ScriptedProvider(
        "groq",
        [
            tool_reply(("search_brandbook", {"query": "фирменные цвета"})),
            reply("Основной цвет — обжаренный кофе #4B2E2A [1]. <confidence>0.9</confidence>"),
        ],
    )
    async with api_with_llm(settings, llm) as client:
        headers = await demo_headers(client, Role.VIEWER)
        conversation_id = await new_conversation(client, headers)
        events = await chat(client, headers, conversation_id, "Какие у бренда фирменные цвета?")

        assert names(events)[0] == "meta"
        assert events[0][1]["tier"] == "simple"
        start = next(data for name, data in events if name == "tool_start")
        assert start["label"] == "Ищу в брендбуке…"
        citations = next(data for name, data in events if name == "citations")
        assert citations["found"] is True
        assert any("#4B2E2A" in item["text"] for item in citations["items"])
        assert names(events)[-1] == "done"

        answer = text_of(events)
        assert "#4B2E2A [1]." in answer
        assert "confidence" not in answer  # the self-assessment marker never reaches the user
        confidence = next(data for name, data in events if name == "confidence")
        assert confidence["self_assessed"] == 0.9
        assert 0 < confidence["retrieval"] <= 1
        assert confidence["low"] is False

        # The model saw only the viewer's tools, without the gateway-controlled client_id,
        # and got numbered fragments to cite.
        first_request = llm.requests[0][1]
        assert {t.name for t in first_request.tools} == {"search_brandbook", "get_client_profile"}
        assert all("client_id" not in t.parameters["properties"] for t in first_request.tools)
        assert "Bean There" in first_request.messages[0].content
        tool_message = llm.requests[1][1].messages[-1]
        assert '"n": 1' in tool_message.content

        detail = (await client.get(f"/conversations/{conversation_id}", headers=headers)).json()
        assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
        assert "confidence" not in detail["messages"][1]["content"]
        assert detail["title"] == "Какие у бренда фирменные цвета?"


async def test_off_topic_question_is_answered_as_not_in_brandbook(settings: Settings) -> None:
    llm = ScriptedProvider(
        "groq",
        [
            tool_reply(("search_brandbook", {"query": "столица Франции"})),
            reply("В брендбуке этого нет. Уточните, пожалуйста, вопрос о бренде."),
        ],
    )
    async with api_with_llm(settings, llm) as client:
        headers = await demo_headers(client, Role.VIEWER)
        conversation_id = await new_conversation(client, headers)
        events = await chat(client, headers, conversation_id, "Какая столица Франции?")

    citations = next(data for name, data in events if name == "citations")
    assert citations == {"items": [], "confidence": citations["confidence"], "found": False}
    tool_message = llm.requests[1][1].messages[-1]
    assert '"found": false' in tool_message.content
    assert "fragments" not in tool_message.content  # nothing weak to improvise from
    confidence = next(data for name, data in events if name == "confidence")
    assert confidence["low"] is True


async def test_content_plan_generation_streams_an_artifact(settings: Settings) -> None:
    llm = ScriptedProvider(
        "groq",
        [
            tool_reply(("create_content_plan", {"start_date": "2026-11-02", "days": 7})),
            reply(PLAN_JSON),  # answered via MCP sampling -> router.chat
            reply("Готово: план на неделю из одной публикации."),
        ],
    )
    async with api_with_llm(settings, llm) as client:
        headers = await demo_headers(client, Role.COPYWRITER)
        conversation_id = await new_conversation(client, headers)
        events = await chat(client, headers, conversation_id, "Составь контент-план на неделю")

    assert events[0][1]["tier"] == "complex"
    artifact = next(data for name, data in events if name == "artifact")
    assert artifact["kind"] == "content_plan"
    assert artifact["data"]["plan"]["items"][0]["format"] == "reels"
    draft_id = artifact["data"]["draft_id"]

    sampling_request = llm.requests[1][1]
    assert sampling_request.json_schema is not None  # provider JSON mode requested
    assert "Bean There" in sampling_request.messages[-1].content
    # The model gets a compact summary, not the full plan texts.
    tool_message = llm.requests[2][1].messages[-1]
    assert tool_message.role == "tool"
    assert draft_id in tool_message.content
    assert "V60" not in tool_message.content


async def test_forbidden_tool_call_is_blocked_by_the_gateway(settings: Settings) -> None:
    llm = ScriptedProvider(
        "groq",
        [
            tool_reply(("publish_content_plan", {"draft_id": "deadbeefdeadbeef"})),
            reply("Публикация недоступна для вашей роли."),
        ],
    )
    async with api_with_llm(settings, llm) as client:
        headers = await demo_headers(client, Role.COPYWRITER)
        conversation_id = await new_conversation(client, headers)
        events = await chat(client, headers, conversation_id, "Опубликуй план")

    assert "tool_start" not in names(events)  # never executed
    end = next(data for name, data in events if name == "tool_end")
    assert end == {
        "id": "call_0",
        "name": "publish_content_plan",
        "ok": False,
        "summary": "Нет доступа",
    }
    tool_message = llm.requests[1][1].messages[-1]
    assert "недоступен" in tool_message.content


async def test_model_cannot_redirect_tools_to_another_client(settings: Settings) -> None:
    async with api_with_llm(settings, ScriptedProvider("groq")) as client:
        admin = await demo_headers(client, Role.ADMIN)
        peakform = next(
            c["id"]
            for c in (await client.get("/clients", headers=admin)).json()
            if c["slug"] == "peakform"
        )
    llm = ScriptedProvider(
        "groq",
        [
            tool_reply(("get_client_profile", {"client_id": peakform})),  # injected attempt
            reply("Профиль получен."),
        ],
    )
    async with api_with_llm(settings, llm) as client:
        headers = await demo_headers(client, Role.VIEWER)
        conversation_id = await new_conversation(client, headers)
        await chat(client, headers, conversation_id, "Покажи профиль PeakForm")

    tool_message = llm.requests[1][1].messages[-1]
    assert (
        '"name": "Bean There"' in tool_message.content
    )  # gateway forced the conversation's client
    assert "PeakForm" not in tool_message.content


async def test_step_limit_stops_runaway_loops(settings: Settings) -> None:
    limit = settings.agent.max_steps
    llm = ScriptedProvider(
        "groq", [tool_reply(("search_brandbook", {"query": f"q{i}"})) for i in range(limit)]
    )
    async with api_with_llm(settings, llm) as client:
        headers = await demo_headers(client, Role.VIEWER)
        conversation_id = await new_conversation(client, headers)
        events = await chat(client, headers, conversation_id, "Расскажи всё")

    error_event = next(data for name, data in events if name == "error")
    assert error_event["code"] == "max_steps"
    assert len(llm.requests) == limit


async def test_llm_outage_gives_friendly_error_and_releases_lock(settings: Settings) -> None:
    llm = ScriptedProvider("groq", [error("auth", retryable=False), reply("Теперь работает.")])
    async with api_with_llm(settings, llm) as client:
        headers = await demo_headers(client, Role.VIEWER)
        conversation_id = await new_conversation(client, headers)
        failed = await chat(client, headers, conversation_id, "Привет")
        recovered = await chat(client, headers, conversation_id, "Привет ещё раз")

    error_event = next(data for name, data in failed if name == "error")
    assert error_event["code"] == "llm_unavailable"
    assert "недоступен" in error_event["message"]
    assert names(recovered)[-1] == "done"


async def test_concurrent_run_is_rejected(settings: Settings) -> None:
    async with api_with_llm(settings, ScriptedProvider("groq")) as client:
        headers = await demo_headers(client, Role.VIEWER)
        conversation_id = await new_conversation(client, headers)
        redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
        await redis.set(f"agent:lock:{conversation_id}", "someone-else", ex=30)
        try:
            response = await client.post(
                f"/conversations/{conversation_id}/messages",
                json={"content": "x"},
                headers=headers,
            )
        finally:
            await redis.delete(f"agent:lock:{conversation_id}")
            await redis.aclose()
    assert response.status_code == 409


async def test_history_is_carried_into_the_next_turn(settings: Settings) -> None:
    llm = ScriptedProvider("groq", [reply("Привет! Чем помочь?"), reply("Ты спрашивал про тон.")])
    async with api_with_llm(settings, llm) as client:
        headers = await demo_headers(client, Role.VIEWER)
        conversation_id = await new_conversation(client, headers)
        await chat(client, headers, conversation_id, "Привет, вопрос про тон")
        await chat(client, headers, conversation_id, "О чём я спрашивал?")

    second = llm.requests[1][1].messages
    assert [m.role for m in second] == ["system", "user", "assistant", "user"]
    assert second[1].content == "Привет, вопрос про тон"


@pytest.mark.parametrize("role", [Role.VIEWER, Role.COPYWRITER])
async def test_conversations_are_private_and_abac_scoped(settings: Settings, role: Role) -> None:
    async with api_with_llm(settings, ScriptedProvider("groq")) as client:
        headers = await demo_headers(client, role)
        admin = await demo_headers(client, Role.ADMIN)
        peakform = next(
            c["id"]
            for c in (await client.get("/clients", headers=admin)).json()
            if c["slug"] == "peakform"
        )
        denied = await client.post("/conversations", json={"client_id": peakform}, headers=headers)
        assert denied.status_code == 404

        admins_conversation = await new_conversation(client, admin)
        foreign = await client.get(f"/conversations/{admins_conversation}", headers=headers)
        assert foreign.status_code == 404
        send = await client.post(
            f"/conversations/{admins_conversation}/messages", json={"content": "x"}, headers=headers
        )
        assert send.status_code == 404


async def test_publish_button_endpoint(settings: Settings) -> None:
    llm = ScriptedProvider(
        "groq",
        [
            tool_reply(("create_content_plan", {"start_date": "2026-11-02"})),
            reply(PLAN_JSON),
            reply("План готов."),
        ],
    )
    async with api_with_llm(settings, llm) as client:
        copywriter = await demo_headers(client, Role.COPYWRITER)
        conversation_id = await new_conversation(client, copywriter)
        events = await chat(client, copywriter, conversation_id, "Составь контент-план")
        artifact = next(data for name, data in events if name == "artifact")
        draft_id = artifact["data"]["draft_id"]
        client_id = (
            await client.get(f"/conversations/{conversation_id}", headers=copywriter)
        ).json()["client_id"]
        body = {"client_id": client_id, "draft_id": draft_id}

        forbidden = await client.post("/plans/publish", json=body, headers=copywriter)
        manager = await demo_headers(client, Role.MANAGER)
        published = await client.post("/plans/publish", json=body, headers=manager)
        plans = await client.get(f"/clients/{client_id}/plans", headers=manager)

    assert forbidden.status_code == 403
    assert published.status_code == 200, published.text
    assert published.json()["already_published"] is False
    assert any(p["title"] == "Неделя тёплых напитков" for p in plans.json())

    engine = create_engine(settings.database_url)
    async with create_sessionmaker(engine)() as session:
        record = (
            await session.scalars(
                select(ContentPlanRecord).where(ContentPlanRecord.source_draft_id == draft_id)
            )
        ).one()
    await engine.dispose()
    assert record.client_id == uuid.UUID(client_id)


def test_sse_parser_handles_crlf() -> None:
    raw = (
        'event: token\r\ndata: {"text": "a"}\r\n\r\n'
        ": ping\r\n\r\n"
        'event: done\r\ndata: {"steps": 1}\r\n\r\n'
    )
    assert parse_sse(raw) == [("token", {"text": "a"}), ("done", {"steps": 1})]
