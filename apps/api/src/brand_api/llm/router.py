"""LLM Router: tier selection, per-call timeouts, retries with exponential backoff, fallback
along a configured model chain, structured output with one self-repair, and call accounting."""

import asyncio
import json
import random
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal

import structlog
from pydantic import BaseModel, ValidationError

from brand_api.llm.base import LLMProvider
from brand_api.llm.complexity import CLASSIFIER_PROMPT, classify_heuristic, parse_classifier_answer
from brand_api.llm.config import LLMSettings
from brand_api.llm.errors import (
    AllModelsFailedError,
    FailedAttempt,
    LLMError,
    NoProviderConfiguredError,
    StreamInterruptedError,
    StructuredOutputError,
)
from brand_api.llm.pricing import cost_usd
from brand_api.llm.types import (
    ChatRequest,
    ChatResponse,
    Message,
    ModelRef,
    StreamDone,
    StreamEvent,
    TextDelta,
    Tier,
)
from brand_api.logging_setup import get_logger
from brand_api.observability.llm_calls import CallRecorder, LLMCallRecord

log = get_logger(__name__)


@dataclass(frozen=True)
class CallContext:
    purpose: str = "agent"
    user_id: uuid.UUID | None = None
    trace_id: str | None = None


@dataclass
class _Outcome:
    ref: ModelRef
    tier: Tier
    attempt: int
    is_fallback: bool
    started: float
    ttft_ms: int | None = None
    streamed: bool = False


class LLMRouter:
    def __init__(
        self,
        settings: LLMSettings,
        providers: Mapping[str, LLMProvider],
        recorder: CallRecorder,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self._providers = dict(providers)
        self._recorder = recorder
        self._sleep = sleep

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()

    # ---- routing -------------------------------------------------------------------------

    def chain(self, tier: Tier) -> list[ModelRef]:
        """Configured chain filtered to providers that have credentials."""
        return [ref for ref in self.settings.chain(tier) if ref.provider in self._providers]

    async def classify(self, text: str, ctx: CallContext | None = None) -> Tier:
        heuristic = classify_heuristic(text)
        if self.settings.classifier == "heuristic":
            return heuristic
        request = ChatRequest(
            messages=[
                Message(role="system", content=CLASSIFIER_PROMPT),
                Message(role="user", content=text[:2000]),
            ],
            max_tokens=256,  # reasoning models spend tokens before the one-word answer
            temperature=0,
        )
        try:
            response = await self.chat(
                request, tier=Tier.SIMPLE, ctx=replace(ctx or CallContext(), purpose="classifier")
            )
        except AllModelsFailedError:
            return heuristic
        return parse_classifier_answer(response.content) or heuristic

    # ---- calls ---------------------------------------------------------------------------

    async def chat(
        self, request: ChatRequest, *, tier: Tier, ctx: CallContext | None = None
    ) -> ChatResponse:
        ctx = ctx or CallContext()
        request = self._with_defaults(request)
        failures: list[FailedAttempt] = []
        for index, ref in enumerate(self._chain_or_raise(tier)):
            provider = self._providers[ref.provider]
            attempt = 0
            while True:
                attempt += 1
                outcome = _Outcome(ref, tier, attempt, index > 0, time.perf_counter())
                try:
                    async with asyncio.timeout(self.settings.timeout_s):
                        response = await provider.chat(ref.model, request)
                except (LLMError, TimeoutError) as exc:
                    error = _as_llm_error(exc)
                    await self._record_error(outcome, error, ctx)
                    delay = self._retry_delay(attempt, error)
                    if delay is None:
                        failures.append(FailedAttempt(str(ref), error.kind, str(error)))
                        break
                    await self._sleep(delay)
                    continue
                await self._record_ok(outcome, response, ctx)
                return response.model_copy(
                    update={"provider": ref.provider, "model": ref.model, "is_fallback": index > 0}
                )
        raise AllModelsFailedError(failures)

    async def stream(
        self, request: ChatRequest, *, tier: Tier, ctx: CallContext | None = None
    ) -> AsyncGenerator[StreamEvent, None]:
        """Fallback/retry is possible only until the first token is emitted."""
        ctx = ctx or CallContext()
        request = self._with_defaults(request)
        failures: list[FailedAttempt] = []
        for index, ref in enumerate(self._chain_or_raise(tier)):
            provider = self._providers[ref.provider]
            attempt = 0
            while True:
                attempt += 1
                outcome = _Outcome(
                    ref, tier, attempt, index > 0, time.perf_counter(), streamed=True
                )
                final: ChatResponse | None = None
                emitted = False
                events = provider.stream(ref.model, request)
                try:
                    while True:
                        try:
                            async with asyncio.timeout(self.settings.timeout_s):  # idle timeout
                                event = await anext(events)
                        except StopAsyncIteration:
                            break
                        if isinstance(event, TextDelta):
                            if outcome.ttft_ms is None:
                                outcome.ttft_ms = _elapsed_ms(outcome.started)
                            emitted = True
                            yield event
                        else:
                            final = event.response
                    if final is None:
                        raise LLMError(
                            "stream ended without a result", kind="empty", retryable=True
                        )
                except (LLMError, TimeoutError) as exc:
                    error = _as_llm_error(exc)
                    await self._record_error(outcome, error, ctx)
                    if emitted:
                        raise StreamInterruptedError(str(error)) from exc
                    delay = self._retry_delay(attempt, error)
                    if delay is None:
                        failures.append(FailedAttempt(str(ref), error.kind, str(error)))
                        break
                    await self._sleep(delay)
                    continue
                finally:
                    await events.aclose()
                await self._record_ok(outcome, final, ctx)
                yield StreamDone(
                    final.model_copy(
                        update={
                            "provider": ref.provider,
                            "model": ref.model,
                            "is_fallback": index > 0,
                        }
                    )
                )
                return
        raise AllModelsFailedError(failures)

    async def structured[M: BaseModel](
        self,
        request: ChatRequest,
        schema: type[M],
        *,
        tier: Tier = Tier.COMPLEX,
        ctx: CallContext | None = None,
    ) -> M:
        """JSON output validated by Pydantic; on failure, one self-repair round, then error."""
        json_schema = schema.model_json_schema()
        instruction = Message(
            role="system",
            content=(
                "Respond with a single JSON object that matches the JSON Schema below. "
                "Output JSON only: no prose, no markdown fences.\n"
                f"Schema:\n{json.dumps(json_schema, ensure_ascii=False)}"
            ),
        )
        request = request.model_copy(
            update={
                "messages": [instruction, *request.messages],
                "json_schema": json_schema,
                "schema_name": schema.__name__,
                "tools": [],
            }
        )
        response = await self.chat(request, tier=tier, ctx=ctx)
        try:
            return parse_json_model(schema, response.content)
        except (ValidationError, ValueError) as first_error:
            log.warning("structured_output_invalid", schema=schema.__name__, attempt=1)
            repair = request.model_copy(
                update={
                    "messages": [
                        *request.messages,
                        Message(role="assistant", content=response.content),
                        Message(
                            role="user",
                            content=(
                                "The previous answer is not valid for the schema:\n"
                                f"{_short_error(first_error)}\n"
                                "Return the corrected JSON object only."
                            ),
                        ),
                    ]
                }
            )
            repaired = await self.chat(repair, tier=tier, ctx=ctx)
            try:
                return parse_json_model(schema, repaired.content)
            except (ValidationError, ValueError) as second_error:
                log.error("structured_output_invalid", schema=schema.__name__, attempt=2)
                raise StructuredOutputError(
                    _short_error(second_error), raw=repaired.content
                ) from second_error

    # ---- internals -----------------------------------------------------------------------

    def _chain_or_raise(self, tier: Tier) -> list[ModelRef]:
        chain = self.chain(tier)
        if not chain:
            raise NoProviderConfiguredError()
        return chain

    def _with_defaults(self, request: ChatRequest) -> ChatRequest:
        update: dict[str, object] = {}
        if request.temperature is None:
            update["temperature"] = self.settings.temperature
        return request.model_copy(update=update) if update else request

    def _retry_delay(self, attempt: int, error: LLMError) -> float | None:
        """Seconds to wait before retrying the same model, or None to move to the fallback."""
        if not error.retryable or attempt > self.settings.max_retries:
            return None
        if error.retry_after is not None:
            # A long Retry-After (e.g. per-minute token quota) is better served by fallback.
            return error.retry_after if error.retry_after <= self.settings.backoff_max_s else None
        base = min(
            self.settings.backoff_max_s, self.settings.backoff_initial_s * 2.0 ** (attempt - 1)
        )
        return base * random.uniform(0.5, 1.0)  # noqa: S311 - jitter, not crypto

    async def _record_ok(self, o: _Outcome, response: ChatResponse, ctx: CallContext) -> None:
        latency = _elapsed_ms(o.started)
        log.info(
            "llm_call",
            model=str(o.ref),
            tier=o.tier.value,
            status="ok",
            attempt=o.attempt,
            fallback=o.is_fallback,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            latency_ms=latency,
        )
        await self._recorder.record(
            self._record(o, ctx, status="ok", error_type=None, response=response, latency=latency)
        )

    async def _record_error(self, o: _Outcome, error: LLMError, ctx: CallContext) -> None:
        latency = _elapsed_ms(o.started)
        log.warning(
            "llm_call",
            model=str(o.ref),
            tier=o.tier.value,
            status="error",
            error_type=error.kind,
            retryable=error.retryable,
            attempt=o.attempt,
            fallback=o.is_fallback,
            latency_ms=latency,
        )
        await self._recorder.record(
            self._record(
                o, ctx, status="error", error_type=error.kind, response=None, latency=latency
            )
        )

    def _record(
        self,
        o: _Outcome,
        ctx: CallContext,
        *,
        status: str,
        error_type: str | None,
        response: ChatResponse | None,
        latency: int,
    ) -> LLMCallRecord:
        usage = response.usage if response else None
        return LLMCallRecord(
            provider=o.ref.provider,
            model=o.ref.model,
            tier=o.tier.value,
            purpose=ctx.purpose,
            status=status,
            error_type=error_type,
            tokens_in=usage.input_tokens if usage else 0,
            tokens_out=usage.output_tokens if usage else 0,
            tokens_estimated=usage.estimated if usage else False,
            cost_usd=cost_usd(self.settings.pricing, o.ref, usage) if usage else Decimal(0),
            latency_ms=latency,
            ttft_ms=o.ttft_ms,
            streamed=o.streamed,
            attempt=o.attempt,
            is_fallback=o.is_fallback,
            user_id=ctx.user_id,
            trace_id=ctx.trace_id,
            request_id=structlog.contextvars.get_contextvars().get("request_id"),
        )


def parse_json_model[M: BaseModel](schema: type[M], text: str) -> M:
    """Validate model output, tolerating markdown fences and leading/trailing prose."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in the response")
    return schema.model_validate_json(text[start : end + 1])


def _as_llm_error(exc: BaseException) -> LLMError:
    if isinstance(exc, LLMError):
        return exc
    return LLMError("call timed out", kind="timeout", retryable=True)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _short_error(error: Exception, limit: int = 800) -> str:
    return str(error)[:limit]
