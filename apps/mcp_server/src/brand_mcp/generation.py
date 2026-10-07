"""Structured generation through MCP sampling: the gateway's LLM router answers, the server
validates with Pydantic and asks for exactly one repair if the JSON doesn't fit the schema."""

from dataclasses import dataclass, field
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.resolve import Sample
from mcp.types import CreateMessageResult, ModelHint, ModelPreferences, SamplingMessage, TextContent
from pydantic import BaseModel, ValidationError

from brand_mcp import prompts
from brand_shared.json_output import parse_json_model
from brand_shared.logging_setup import get_logger

log = get_logger("brand_mcp.generation")

COMPLEX = ModelPreferences(hints=[ModelHint(name="complex")], intelligence_priority=0.9)


@dataclass(frozen=True)
class GenerationPrompt:
    system: str
    user: str
    schema: type[BaseModel]
    max_tokens: int
    purpose: str
    json_schema: dict[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "json_schema", self.schema.model_json_schema())

    def sample(self, messages: list[SamplingMessage]) -> Sample:
        return Sample(
            messages,
            max_tokens=self.max_tokens,
            system_prompt=self.system,
            temperature=0.7,
            model_preferences=COMPLEX,
            metadata={
                "json_schema": self.json_schema,
                "schema_name": self.schema.__name__,
                "purpose": self.purpose,
            },
        )


def _text(message: str, role: str = "user") -> SamplingMessage:
    return SamplingMessage(role=role, content=TextContent(type="text", text=message))


def result_text(result: CreateMessageResult) -> str:
    content = result.content
    return content.text if isinstance(content, TextContent) else ""


def first_sample(prompt: GenerationPrompt) -> Sample:
    return prompt.sample([_text(prompt.user)])


def accept_or_repair(
    prompt: GenerationPrompt, first: CreateMessageResult
) -> CreateMessageResult | Sample:
    text = result_text(first)
    try:
        parse_json_model(prompt.schema, text)
    except (ValidationError, ValueError) as exc:
        log.warning("generation_invalid", purpose=prompt.purpose, attempt=1, error=str(exc)[:300])
        return prompt.sample(
            [_text(prompt.user), _text(text, role="assistant"), _text(prompts.repair(str(exc)))]
        )
    return first


def parse_final[M: BaseModel](schema: type[M], result: CreateMessageResult) -> M:
    try:
        return parse_json_model(schema, result_text(result))
    except (ValidationError, ValueError) as exc:
        log.error("generation_invalid", schema=schema.__name__, attempt=2, error=str(exc)[:300])
        raise ToolError(
            "Модель вернула некорректный результат даже после исправления. "
            "Попробуйте переформулировать запрос."
        ) from exc
