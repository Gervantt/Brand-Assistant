"""Langfuse tracing (OpenTelemetry under the hood). One trace per agent request, keyed by the
same `trace_id` used in logs and `llm_calls`; generations for every LLM attempt (including MCP
sampling), tool spans for every tool call. Without keys everything is a no-op."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal, Protocol

from langfuse import Langfuse

from brand_shared.logging_setup import get_logger

log = get_logger(__name__)

ObservationType = Literal["span", "generation", "tool", "agent"]
MAX_FIELD_CHARS = 4000


class Observation(Protocol):
    def update(self, **fields: Any) -> Any: ...


class _NullObservation:
    def update(self, **fields: Any) -> None:
        return None


class Tracer:
    def __init__(self, client: Langfuse | None = None) -> None:
        self.client = client

    @property
    def enabled(self) -> bool:
        return self.client is not None

    @classmethod
    def create(
        cls,
        *,
        public_key: str | None,
        secret_key: str | None,
        host: str | None,
        environment: str,
        release: str,
    ) -> "Tracer":
        if not (public_key and secret_key):
            log.info("tracing_disabled", reason="LANGFUSE keys are not set")
            return cls(None)
        client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host or "https://cloud.langfuse.com",
            environment=environment,
            release=release,
        )
        log.info("tracing_enabled", host=host)
        return cls(client)

    @contextmanager
    def observe(
        self,
        name: str,
        *,
        as_type: ObservationType = "span",
        trace_id: str | None = None,
        **fields: Any,
    ) -> Iterator[Observation]:
        """Child of the current observation, or the root of `trace_id` when given."""
        if self.client is None:
            yield _NullObservation()
            return
        trace_context = {"trace_id": trace_id} if trace_id else None
        # The SDK types this with one overload per observation type; we pass it through.
        start: Any = self.client.start_as_current_observation
        with start(
            name=name,
            as_type=as_type,
            trace_context=trace_context,
            **{k: clip(v) for k, v in fields.items()},
        ) as observation:
            yield observation

    def flush(self) -> None:
        if self.client is not None:
            self.client.flush()

    def shutdown(self) -> None:
        if self.client is not None:
            self.client.shutdown()


def clip(value: Any) -> Any:
    """Keep traces light: long strings (documents, plans) are truncated."""
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + "…"
    if isinstance(value, list):
        return [clip(v) for v in value]
    if isinstance(value, dict):
        return {k: clip(v) for k, v in value.items()}
    return value


NULL_TRACER = Tracer(None)
