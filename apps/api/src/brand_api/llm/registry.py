"""Builds provider clients from settings. Missing credentials simply disable a provider."""

from collections.abc import Mapping

from brand_api.config import Settings
from brand_api.limits.budget import TokenBudget
from brand_api.llm.anthropic_provider import AnthropicProvider
from brand_api.llm.base import LLMProvider
from brand_api.llm.cache import ResponseCache
from brand_api.llm.openai_compat import GROQ_BASE_URL, OpenAICompatOptions, OpenAICompatProvider
from brand_api.llm.router import LLMRouter
from brand_api.observability.llm_calls import CallRecorder
from brand_api.observability.tracing import NULL_TRACER, Tracer


def build_providers(settings: Settings) -> dict[str, LLMProvider]:
    timeout = settings.llm.timeout_s
    providers: dict[str, LLMProvider] = {}
    if settings.groq_api_key:
        providers["groq"] = OpenAICompatProvider(
            "groq",
            api_key=settings.groq_api_key.get_secret_value(),
            base_url=GROQ_BASE_URL,
            timeout_s=timeout,
        )
    if settings.openai_api_key:
        providers["openai"] = OpenAICompatProvider(
            "openai",
            api_key=settings.openai_api_key.get_secret_value(),
            timeout_s=timeout,
            options=OpenAICompatOptions(send_temperature=False),
        )
    if settings.anthropic_api_key:
        providers["anthropic"] = AnthropicProvider(
            api_key=settings.anthropic_api_key.get_secret_value(), timeout_s=timeout
        )
    if settings.ollama_base_url:
        providers["ollama"] = OpenAICompatProvider(
            "ollama",
            api_key="ollama",  # required by the SDK, ignored by Ollama
            base_url=settings.ollama_base_url.rstrip("/") + "/v1",
            timeout_s=max(timeout, 120.0),  # local CPU inference is slow
            options=OpenAICompatOptions(max_tokens_param="max_tokens"),
        )
    return providers


def build_router(
    settings: Settings,
    recorder: CallRecorder,
    providers: Mapping[str, LLMProvider] | None = None,
    *,
    tracer: Tracer = NULL_TRACER,
    cache: ResponseCache | None = None,
    budget: TokenBudget | None = None,
) -> LLMRouter:
    """`providers` overrides credential-based discovery (tests inject scripted providers)."""
    return LLMRouter(
        settings.llm,
        build_providers(settings) if providers is None else providers,
        recorder,
        tracer=tracer,
        cache=cache if settings.llm.cache_enabled else None,
        budget=budget,
    )
