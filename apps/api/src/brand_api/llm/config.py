"""LLM routing configuration. Switching provider is one line: `llm.provider` (or LLM__PROVIDER)."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from brand_api.llm.types import ModelRef, Tier


class TierModels(BaseModel):
    simple: str
    complex: str

    def for_tier(self, tier: Tier) -> str:
        return self.simple if tier is Tier.SIMPLE else self.complex


class ModelPrice(BaseModel):
    """USD per 1M tokens."""

    input: float = Field(ge=0)
    output: float = Field(ge=0)


DEFAULT_PROVIDER_MODELS: dict[str, TierModels] = {
    "groq": TierModels(simple="openai/gpt-oss-20b", complex="openai/gpt-oss-120b"),
    "openai": TierModels(simple="gpt-6-luna", complex="gpt-6.1-sol"),
    "anthropic": TierModels(simple="claude-haiku-4-5", complex="claude-opus-5-5"),
    "ollama": TierModels(simple="qwen3:4b", complex="qwen3:8b"),
}

# Public list prices (USD / 1M tokens), checked 2026-10. Update when providers change them.
DEFAULT_PRICING: dict[str, ModelPrice] = {
    "groq/openai/gpt-oss-20b": ModelPrice(input=0.075, output=0.30),
    "groq/openai/gpt-oss-120b": ModelPrice(input=0.15, output=0.60),
    "groq/qwen/qwen3.8-27b": ModelPrice(input=0.60, output=2.40),
    "openai/gpt-6-luna": ModelPrice(input=0.10, output=0.50),
    "openai/gpt-6.1-sol": ModelPrice(input=2.00, output=10.00),
    "anthropic/claude-haiku-4-5": ModelPrice(input=1.00, output=5.00),
    "anthropic/claude-sonnet-5-5": ModelPrice(input=2.00, output=10.00),
    "anthropic/claude-opus-5-5": ModelPrice(input=4.00, output=20.00),
    "ollama/qwen3:4b": ModelPrice(input=0, output=0),
    "ollama/qwen3:8b": ModelPrice(input=0, output=0),
}


class LLMSettings(BaseModel):
    provider: str = "groq"
    providers: dict[str, TierModels] = Field(default_factory=lambda: dict(DEFAULT_PROVIDER_MODELS))
    # Tried in order after the primary. Entry = provider name (uses its tier model) or exact
    # `provider/model`. A second model on the same provider helps with per-model rate limits.
    fallback: list[str] = Field(default_factory=list)
    classifier: Literal["heuristic", "llm"] = "heuristic"
    timeout_s: float = Field(default=45.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)
    backoff_initial_s: float = Field(default=0.5, ge=0)
    backoff_max_s: float = Field(default=8.0, ge=0)
    max_tokens: int = Field(default=2048, gt=0)
    temperature: float = Field(default=0.4, ge=0, le=2)
    pricing: dict[str, ModelPrice] = Field(default_factory=lambda: dict(DEFAULT_PRICING))

    @model_validator(mode="after")
    def _validate_refs(self) -> "LLMSettings":
        if self.provider not in self.providers:
            raise ValueError(f"llm.provider={self.provider!r} has no entry in llm.providers")
        for entry in self.fallback:
            if "/" in entry:
                ModelRef.parse(entry)
            elif entry not in self.providers:
                raise ValueError(f"llm.fallback entry {entry!r} is not a known provider")
        return self

    def chain(self, tier: Tier) -> list[ModelRef]:
        """Primary model for the tier followed by the fallback chain, de-duplicated."""
        refs = [ModelRef(self.provider, self.providers[self.provider].for_tier(tier))]
        for entry in self.fallback:
            if "/" in entry:
                refs.append(ModelRef.parse(entry))
            else:
                refs.append(ModelRef(entry, self.providers[entry].for_tier(tier)))
        return list(dict.fromkeys(refs))  # de-duplicate, keep order
