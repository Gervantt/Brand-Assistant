"""Tool exposure policy: what the model sees, what it may call, and what it gets back."""

import copy
import json
from typing import Any

from mcp.types import Tool as McpTool

from brand_api.auth.principal import Principal
from brand_api.llm.types import ToolSpec
from brand_shared.permissions import Tool, llm_tools_for

# Injected by the gateway from the conversation, never chosen by the model: a prompt-injected
# model cannot point a tool at another client.
GATEWAY_ARGS = frozenset({"client_id"})

STATUS_LABELS: dict[str, str] = {
    Tool.SEARCH_BRANDBOOK: "Ищу в брендбуке…",
    Tool.GET_CLIENT_PROFILE: "Смотрю профиль клиента…",
    Tool.CREATE_CONTENT_PLAN: "Составляю контент-план…",
    Tool.WRITE_POST: "Пишу текст…",
    Tool.CREATE_DESIGNER_BRIEF: "Готовлю бриф для дизайнера…",
    Tool.PUBLISH_CONTENT_PLAN: "Публикую контент-план…",
}

ARTIFACT_KINDS: dict[str, str] = {
    Tool.CREATE_CONTENT_PLAN: "content_plan",
    Tool.WRITE_POST: "post",
    Tool.CREATE_DESIGNER_BRIEF: "designer_brief",
    Tool.PUBLISH_CONTENT_PLAN: "publication",
}

GENERATION_TOOLS = frozenset(
    {Tool.CREATE_CONTENT_PLAN, Tool.WRITE_POST, Tool.CREATE_DESIGNER_BRIEF}
)


def model_tool_specs(mcp_tools: list[McpTool], principal: Principal) -> list[ToolSpec]:
    """Filter the MCP catalogue by role *before* the model sees it, and hide gateway args."""
    allowed = {t.value for t in llm_tools_for(principal.role)}
    specs: list[ToolSpec] = []
    for tool in mcp_tools:
        if tool.name not in allowed:
            continue
        schema = copy.deepcopy(tool.input_schema)
        properties: dict[str, Any] = schema.get("properties", {})
        for arg in GATEWAY_ARGS:
            properties.pop(arg, None)
        if "required" in schema:
            schema["required"] = [r for r in schema["required"] if r not in GATEWAY_ARGS]
        specs.append(
            ToolSpec(name=tool.name, description=tool.description or "", parameters=schema)
        )
    return specs


def for_model(tool_name: str, data: dict[str, Any] | None, *, max_chars: int) -> str:
    """What the model reads back. Big artifacts are already rendered for the user, so the model
    gets a compact summary (saves tokens on small free-tier quotas)."""
    if data is None:
        return "{}"
    view: Any = data
    if tool_name == Tool.CREATE_CONTENT_PLAN:
        plan = data.get("plan", {})
        view = {
            "draft_id": data.get("draft_id"),
            "title": plan.get("title"),
            "period": [plan.get("period_start"), plan.get("period_end")],
            "items": [
                {k: item.get(k) for k in ("date", "platform", "format", "rubric", "title")}
                for item in plan.get("items", [])
            ],
            "note": "Полный план показан пользователю таблицей; кратко резюмируй его.",
        }
    elif tool_name == Tool.CREATE_DESIGNER_BRIEF:
        brief = data.get("brief", {})
        view = {
            "title": brief.get("title"),
            "dimensions": brief.get("dimensions"),
            "note": "Бриф показан пользователю карточкой; кратко резюмируй его.",
        }
    text = json.dumps(view, ensure_ascii=False)
    return text if len(text) <= max_chars else text[:max_chars] + "…(обрезано)"
