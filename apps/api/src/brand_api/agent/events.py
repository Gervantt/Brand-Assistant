"""Server-sent events emitted by the agent. `event` names are the SSE event types."""

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentEvent:
    event: str = field(init=False)

    def sse(self) -> dict[str, str]:
        data = {k: v for k, v in asdict(self).items() if k != "event"}
        return {"event": self.event, "data": json.dumps(data, ensure_ascii=False, default=str)}


@dataclass(frozen=True)
class MetaEvent(AgentEvent):
    event: str = field(init=False, default="meta")
    conversation_id: str
    trace_id: str
    tier: str


@dataclass(frozen=True)
class StatusEvent(AgentEvent):
    event: str = field(init=False, default="status")
    text: str


@dataclass(frozen=True)
class TokenEvent(AgentEvent):
    event: str = field(init=False, default="token")
    text: str


@dataclass(frozen=True)
class ToolStartEvent(AgentEvent):
    event: str = field(init=False, default="tool_start")
    id: str
    name: str
    label: str


@dataclass(frozen=True)
class ToolEndEvent(AgentEvent):
    event: str = field(init=False, default="tool_end")
    id: str
    name: str
    ok: bool
    summary: str = ""


@dataclass(frozen=True)
class ArtifactEvent(AgentEvent):
    event: str = field(init=False, default="artifact")
    kind: str
    data: dict[str, Any]


@dataclass(frozen=True)
class CitationsEvent(AgentEvent):
    event: str = field(init=False, default="citations")
    items: list[dict[str, Any]]
    confidence: float
    found: bool


@dataclass(frozen=True)
class ErrorEvent(AgentEvent):
    event: str = field(init=False, default="error")
    message: str
    code: str


@dataclass(frozen=True)
class DoneEvent(AgentEvent):
    event: str = field(init=False, default="done")
    steps: int
    message_id: str | None = None
