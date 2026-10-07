"""Provider-neutral message, tool and response types."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Tier(StrEnum):
    SIMPLE = "simple"
    COMPLEX = "complex"


@dataclass(frozen=True, slots=True)
class ModelRef:
    """`provider/model`. Only the first slash separates them: `groq/openai/gpt-oss-120b`."""

    provider: str
    model: str

    @classmethod
    def parse(cls, value: str) -> "ModelRef":
        provider, _, model = value.strip().partition("/")
        if not provider or not model:
            raise ValueError(f"model ref must look like 'provider/model', got {value!r}")
        return cls(provider=provider, model=model)

    def __str__(self) -> str:
        return f"{self.provider}/{self.model}"


class NativeContent(BaseModel):
    """Provider-native assistant content (e.g. Anthropic thinking blocks).

    Some providers require their own blocks to be replayed verbatim in multi-step tool loops.
    It is only replayed to the exact model that produced it; other models get the neutral form.
    """

    model: str
    blocks: list[dict[str, Any]]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    parse_error: str | None = None  # set when the model produced non-JSON arguments


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    native: NativeContent | None = None


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class ChatRequest(BaseModel):
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    max_tokens: int = 2048
    temperature: float | None = None
    json_schema: dict[str, Any] | None = None  # request JSON output matching this schema
    schema_name: str = "result"


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    estimated: bool = False  # provider did not report usage; counted by heuristic


class ChatResponse(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    finish_reason: str | None = None
    native: NativeContent | None = None
    provider: str = ""
    model: str = ""
    is_fallback: bool = False

    def as_message(self) -> Message:
        return Message(
            role="assistant", content=self.content, tool_calls=self.tool_calls, native=self.native
        )


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class StreamDone:
    response: ChatResponse


StreamEvent = TextDelta | StreamDone


def estimate_tokens(text: str) -> int:
    """Rough fallback when a provider omits usage (~4 chars/token, Cyrillic is denser)."""
    return max(1, len(text) // 3)
