"""Generation tools. Resolver DAG per tool:

    authorize -> prompt (brand context from DB) -> first sample -> accept or one repair sample

`authorize` runs first so a denied call never reaches the LLM.
"""

from datetime import date, timedelta
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.resolve import Resolve, Sample
from mcp.types import CreateMessageResult, ToolAnnotations
from pydantic import Field

from brand_mcp import prompts
from brand_mcp.auth import Authorized, authorize
from brand_mcp.brand_context import load_brand_context
from brand_mcp.deps import Deps
from brand_mcp.drafts import save_draft
from brand_mcp.generation import GenerationPrompt, accept_or_repair, first_sample, parse_final
from brand_mcp.tool_log import tool_call
from brand_mcp.tools.common import ClientId
from brand_shared.permissions import Tool
from brand_shared.schemas.content import ContentFormat, ContentPlan, DesignerBrief, Post
from brand_shared.schemas.tools import ContentPlanDraft, DesignerBriefResult, PostResult

GENERATES = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=False)

Days = Annotated[int, Field(ge=1, le=31, description="Длина периода в днях")]
PostsPerWeek = Annotated[int, Field(ge=1, le=14, description="Публикаций в неделю")]
Platforms = Annotated[
    list[str] | None, Field(description="Площадки; по умолчанию — площадки из профиля бренда")
]
Platform = Annotated[str, Field(min_length=2, max_length=40)]
Text500 = Annotated[str, Field(max_length=500)]


def register(server: MCPServer, deps: Deps) -> None:
    secret = deps.settings.mcp_internal_secret.get_secret_value()
    max_tokens = deps.settings.generation_max_tokens

    # ---- create_content_plan -----------------------------------------------------------

    def plan_auth(ctx: Context, client_id: str) -> Authorized:
        return authorize(ctx, secret, Tool.CREATE_CONTENT_PLAN, client_id)

    async def plan_prompt(
        auth: Annotated[Authorized, Resolve(plan_auth)],
        start_date: date | None,
        days: int,
        platforms: list[str] | None,
        posts_per_week: int,
        goal: str,
    ) -> GenerationPrompt:
        brand = await load_brand_context(deps, auth.client_id, goal or "рубрики и тон голоса")
        start = start_date or date.today()
        system, user = prompts.content_plan(
            brand.render(),
            start=start,
            end=start + timedelta(days=days - 1),
            platforms=platforms or brand.profile.platforms or ["Instagram"],
            posts_per_week=posts_per_week,
            goal=goal,
        )
        return GenerationPrompt(system, user, ContentPlan, max_tokens, "content_plan")

    def plan_first(prompt: Annotated[GenerationPrompt, Resolve(plan_prompt)]) -> Sample:
        return first_sample(prompt)

    def plan_final(
        prompt: Annotated[GenerationPrompt, Resolve(plan_prompt)],
        first: Annotated[CreateMessageResult, Resolve(plan_first)],
    ) -> CreateMessageResult | Sample:
        return accept_or_repair(prompt, first)

    @server.tool(name=Tool.CREATE_CONTENT_PLAN.value, annotations=GENERATES)
    async def create_content_plan(
        client_id: ClientId,
        auth: Annotated[Authorized, Resolve(plan_auth)],
        result: Annotated[CreateMessageResult, Resolve(plan_final)],
        start_date: Annotated[date | None, Field(description="Начало периода, YYYY-MM-DD")] = None,
        days: Days = 7,
        platforms: Platforms = None,
        posts_per_week: PostsPerWeek = 3,
        goal: Text500 = "",
    ) -> ContentPlanDraft:
        """Составляет контент-план (дата, площадка, формат, рубрика, текст, хэштеги, идея
        визуала) и сохраняет его как черновик. Возвращает draft_id для публикации."""
        async with tool_call(auth, days=days):
            plan = parse_final(ContentPlan, result)
            draft_id = await save_draft(
                deps.redis,
                client_id=auth.client_id,
                created_by=auth.claims.user_id,
                plan=plan,
                ttl_s=deps.settings.draft_ttl_s,
            )
            return ContentPlanDraft(draft_id=draft_id, plan=plan)

    # ---- write_post ---------------------------------------------------------------------

    def post_auth(ctx: Context, client_id: str) -> Authorized:
        return authorize(ctx, secret, Tool.WRITE_POST, client_id)

    async def post_prompt(
        auth: Annotated[Authorized, Resolve(post_auth)],
        topic: str,
        platform: str,
        format: str,
        key_message: str,
    ) -> GenerationPrompt:
        brand = await load_brand_context(deps, auth.client_id, topic)
        system, user = prompts.post(
            brand.render(), topic=topic, platform=platform, fmt=format, key_message=key_message
        )
        return GenerationPrompt(system, user, Post, max_tokens, "post")

    def post_first(prompt: Annotated[GenerationPrompt, Resolve(post_prompt)]) -> Sample:
        return first_sample(prompt)

    def post_final(
        prompt: Annotated[GenerationPrompt, Resolve(post_prompt)],
        first: Annotated[CreateMessageResult, Resolve(post_first)],
    ) -> CreateMessageResult | Sample:
        return accept_or_repair(prompt, first)

    @server.tool(name=Tool.WRITE_POST.value, annotations=GENERATES)
    async def write_post(
        client_id: ClientId,
        topic: Annotated[str, Field(min_length=3, max_length=500, description="Тема публикации")],
        auth: Annotated[Authorized, Resolve(post_auth)],
        result: Annotated[CreateMessageResult, Resolve(post_final)],
        platform: Platform = "Instagram",
        format: ContentFormat = "post",
        key_message: Text500 = "",
    ) -> PostResult:
        """Пишет текст поста или сторис в тоне бренда: заголовок, текст, призыв к действию,
        хэштеги и идею визуала."""
        async with tool_call(auth, platform=platform, format=format):
            return PostResult(post=parse_final(Post, result))

    # ---- create_designer_brief ----------------------------------------------------------

    def brief_auth(ctx: Context, client_id: str) -> Authorized:
        return authorize(ctx, secret, Tool.CREATE_DESIGNER_BRIEF, client_id)

    async def brief_prompt(
        auth: Annotated[Authorized, Resolve(brief_auth)],
        post_text: str,
        platform: str,
        format: str,
        notes: str,
    ) -> GenerationPrompt:
        brand = await load_brand_context(deps, auth.client_id, "визуальный стиль цвета айдентика")
        system, user = prompts.designer_brief(
            brand.render(), post_text=post_text, platform=platform, fmt=format, notes=notes
        )
        return GenerationPrompt(system, user, DesignerBrief, max_tokens, "designer_brief")

    def brief_first(prompt: Annotated[GenerationPrompt, Resolve(brief_prompt)]) -> Sample:
        return first_sample(prompt)

    def brief_final(
        prompt: Annotated[GenerationPrompt, Resolve(brief_prompt)],
        first: Annotated[CreateMessageResult, Resolve(brief_first)],
    ) -> CreateMessageResult | Sample:
        return accept_or_repair(prompt, first)

    @server.tool(name=Tool.CREATE_DESIGNER_BRIEF.value, annotations=GENERATES)
    async def create_designer_brief(
        client_id: ClientId,
        post_text: Annotated[
            str, Field(min_length=10, max_length=3000, description="Текст или идея публикации")
        ],
        auth: Annotated[Authorized, Resolve(brief_auth)],
        result: Annotated[CreateMessageResult, Resolve(brief_final)],
        platform: Platform = "Instagram",
        format: ContentFormat = "post",
        notes: Text500 = "",
    ) -> DesignerBriefResult:
        """Готовит бриф для дизайнера по конкретной публикации: размеры, цель, ключевое
        сообщение, текст на визуале, описание, настроение, цвета, do/don't, файлы на выходе."""
        async with tool_call(auth, platform=platform):
            return DesignerBriefResult(brief=parse_final(DesignerBrief, result))
