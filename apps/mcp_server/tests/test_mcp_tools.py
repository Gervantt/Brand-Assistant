import json
from datetime import date

import httpx2
from conftest import (
    TEST_DATABASE_URL,
    TEST_REDIS_URL,
    DemoIds,
    connect,
    data,
    sampled_text,
    text_of,
    token_for,
)
from mcp.client import ClientRequestContext
from mcp.types import CreateMessageRequestParams, CreateMessageResult, TextContent
from redis.asyncio import Redis
from sqlalchemy import select

from brand_mcp.drafts import load_draft
from brand_shared.db.engine import create_engine, create_sessionmaker
from brand_shared.db.models import ContentPlanRecord
from brand_shared.permissions import Role

START = date(2026, 11, 2)

VALID_PLAN = {
    "title": "Ноябрь: тёплые напитки",
    "period_start": "2026-11-02",
    "period_end": "2026-11-08",
    "goal": "Вовлечение",
    "items": [
        {
            "date": "2026-11-03",
            "platform": "Instagram",
            "format": "carousel",
            "rubric": "Сезонное меню",
            "title": "Пряный латте вернулся",
            "text": "Наш пряный латте снова в меню — заходи согреться после пар!",
            "hashtags": ["BeanThere", "#кофезнаток"],
            "visual_idea": "Чашка латте на подоконнике, за окном первый снег",
        }
    ],
}


class ScriptedSampler:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.requests: list[CreateMessageRequestParams] = []

    async def __call__(
        self, context: ClientRequestContext, params: CreateMessageRequestParams
    ) -> CreateMessageResult:
        self.requests.append(params)
        return CreateMessageResult(
            role="assistant",
            content=TextContent(type="text", text=self.answers.pop(0)),
            model="scripted",
        )


async def test_requests_without_service_token_are_rejected(mcp_url: str) -> None:
    async with httpx2.AsyncClient() as http:
        response = await http.post(mcp_url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 401


async def test_forged_token_is_rejected(mcp_url: str) -> None:
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer not-a-jwt"}) as http:
        response = await http.post(mcp_url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 401


async def test_tool_catalog(mcp_url: str, ids: DemoIds) -> None:
    async with connect(mcp_url, token_for(ids, Role.VIEWER)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {
        "search_brandbook",
        "get_client_profile",
        "create_content_plan",
        "write_post",
        "create_designer_brief",
        "publish_content_plan",
        "ingest_document",  # internal: the gateway never offers it to the model
    }
    plan_schema = tools["create_content_plan"].input_schema
    assert "client_id" in plan_schema["properties"]
    assert "auth" not in plan_schema["properties"]  # resolver params are not model-visible
    assert "result" not in plan_schema["properties"]
    annotations = tools["publish_content_plan"].annotations
    assert annotations is not None
    assert annotations.read_only_hint is False


async def test_profile_respects_client_access(mcp_url: str, ids: DemoIds) -> None:
    async with connect(mcp_url, token_for(ids, Role.VIEWER)) as client:
        own = await client.call_tool(
            "get_client_profile", {"client_id": str(ids.clients["bean-there"])}
        )
        foreign = await client.call_tool(
            "get_client_profile", {"client_id": str(ids.clients["peakform"])}
        )
    assert not own.is_error
    assert data(own)["name"] == "Bean There"
    assert foreign.is_error
    assert "Клиент недоступен" in text_of(foreign)


async def test_denied_generation_never_samples_the_llm(mcp_url: str, ids: DemoIds) -> None:
    sampler = ScriptedSampler()
    async with connect(mcp_url, token_for(ids, Role.VIEWER), sampler) as client:
        result = await client.call_tool(
            "create_content_plan", {"client_id": str(ids.clients["bean-there"])}
        )
    assert result.is_error
    assert "Недостаточно прав" in text_of(result)
    assert sampler.requests == []


async def test_content_plan_is_repaired_once_and_saved_as_draft(mcp_url: str, ids: DemoIds) -> None:
    sampler = ScriptedSampler('{"title": "сломанный"}', json.dumps(VALID_PLAN, ensure_ascii=False))
    async with connect(mcp_url, token_for(ids, Role.COPYWRITER), sampler) as client:
        result = await client.call_tool(
            "create_content_plan",
            {
                "client_id": str(ids.clients["bean-there"]),
                "start_date": START.isoformat(),
                "days": 7,
                "goal": "Вовлечение",
            },
        )

    assert not result.is_error, result.content
    first, repair = sampler.requests
    assert (first.metadata or {})["schema_name"] == "ContentPlan"
    assert first.model_preferences is not None
    assert (first.model_preferences.hints or [])[0].name == "complex"
    assert "Bean There" in sampled_text(first)  # brand context injected
    assert "2026-11-02" in sampled_text(first)
    assert len(repair.messages) == 3  # original prompt, bad answer, repair instruction
    plan = data(result)["plan"]
    assert plan["items"][0]["hashtags"][0] == "#BeanThere"  # normalised

    redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    draft = await load_draft(redis, data(result)["draft_id"])
    await redis.aclose()
    assert draft is not None
    assert draft.client_id == ids.clients["bean-there"]


async def test_generation_fails_after_second_invalid_answer(mcp_url: str, ids: DemoIds) -> None:
    sampler = ScriptedSampler("не JSON", "всё ещё не JSON")
    async with connect(mcp_url, token_for(ids, Role.COPYWRITER), sampler) as client:
        result = await client.call_tool(
            "write_post", {"client_id": str(ids.clients["bean-there"]), "topic": "осенний латте"}
        )
    assert result.is_error
    assert "некорректный результат" in text_of(result)
    assert len(sampler.requests) == 2


async def _make_draft(mcp_url: str, ids: DemoIds) -> str:
    sampler = ScriptedSampler(json.dumps(VALID_PLAN, ensure_ascii=False))
    async with connect(mcp_url, token_for(ids, Role.COPYWRITER), sampler) as client:
        result = await client.call_tool(
            "create_content_plan",
            {"client_id": str(ids.clients["bean-there"]), "start_date": START.isoformat()},
        )
    return str(data(result)["draft_id"])


async def test_publish_requires_manager_and_is_idempotent(mcp_url: str, ids: DemoIds) -> None:
    draft_id = await _make_draft(mcp_url, ids)
    bean_there = str(ids.clients["bean-there"])

    async with connect(mcp_url, token_for(ids, Role.COPYWRITER)) as client:
        denied = await client.call_tool(
            "publish_content_plan", {"client_id": bean_there, "draft_id": draft_id}
        )
    assert denied.is_error

    async with connect(mcp_url, token_for(ids, Role.MANAGER)) as client:
        wrong_client = await client.call_tool(
            "publish_content_plan",
            {"client_id": str(ids.clients["peakform"]), "draft_id": draft_id},
        )
        first = await client.call_tool(
            "publish_content_plan", {"client_id": bean_there, "draft_id": draft_id}
        )
        second = await client.call_tool(
            "publish_content_plan", {"client_id": bean_there, "draft_id": draft_id}
        )

    assert wrong_client.is_error
    assert "другому клиенту" in text_of(wrong_client)
    assert not first.is_error
    assert data(first)["already_published"] is False
    assert data(second)["already_published"] is True
    assert data(first)["plan_id"] == data(second)["plan_id"]

    engine = create_engine(TEST_DATABASE_URL)
    async with create_sessionmaker(engine)() as session:
        rows = (
            await session.scalars(
                select(ContentPlanRecord).where(ContentPlanRecord.source_draft_id == draft_id)
            )
        ).all()
    await engine.dispose()
    assert len(rows) == 1
    assert rows[0].approved_by == ids.users["manager@demo.com"]
    assert rows[0].created_by == ids.users["copywriter@demo.com"]


async def test_unknown_draft_is_a_clear_error(mcp_url: str, ids: DemoIds) -> None:
    async with connect(mcp_url, token_for(ids, Role.MANAGER)) as client:
        result = await client.call_tool(
            "publish_content_plan",
            {"client_id": str(ids.clients["bean-there"]), "draft_id": "deadbeefdeadbeef"},
        )
    assert result.is_error
    assert "не найден" in text_of(result)


class BrokenRetriever:
    threshold = 0.4

    async def search(self, *args: object, **kwargs: object) -> list[object]:
        raise RuntimeError("embeddings API returned 400")


async def test_generation_context_survives_a_retrieval_outage(ids: DemoIds) -> None:
    from conftest import mcp_settings_for_tests  # noqa: PLC0415

    from brand_mcp.brand_context import load_brand_context  # noqa: PLC0415
    from brand_mcp.deps import Deps  # noqa: PLC0415

    deps = Deps.create(mcp_settings_for_tests())
    deps.retriever = BrokenRetriever()  # type: ignore[assignment]
    context = await load_brand_context(deps, ids.clients["bean-there"], "осенний латте")
    await deps.aclose()
    assert context.hits == []
    assert "Bean There" in context.render()  # still has the brand profile to write from
