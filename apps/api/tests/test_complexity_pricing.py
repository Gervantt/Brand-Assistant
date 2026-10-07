from decimal import Decimal

import pytest

from brand_api.llm.complexity import classify_heuristic, parse_classifier_answer
from brand_api.llm.config import DEFAULT_PRICING
from brand_api.llm.pricing import cost_usd
from brand_api.llm.types import ModelRef, Tier, Usage


@pytest.mark.parametrize(
    "text",
    [
        "Составь контент-план на неделю для Instagram",
        "Напиши пост про осеннее меню",
        "Подготовь бриф для дизайнера по посту №3",
        "Придумай 5 идей для сторис",
        "Сделай план публикаций на месяц",
        "Write a content plan",
    ],
)
def test_generation_requests_are_complex(text: str) -> None:
    assert classify_heuristic(text) is Tier.COMPLEX


@pytest.mark.parametrize(
    "text",
    [
        "Какой тон голоса у бренда?",
        "Какие цвета нельзя использовать?",
        "Кто целевая аудитория?",
        "Можно ли использовать эмодзи?",
    ],
)
def test_brandbook_questions_are_simple(text: str) -> None:
    assert classify_heuristic(text) is Tier.SIMPLE


def test_long_or_multi_question_requests_are_complex() -> None:
    assert classify_heuristic("Расскажи подробно о бренде. " * 40) is Tier.COMPLEX
    assert classify_heuristic("Какой тон? Какие цвета? Какая аудитория?") is Tier.COMPLEX


@pytest.mark.parametrize(
    ("answer", "tier"),
    [("complex", Tier.COMPLEX), (" Simple.", Tier.SIMPLE), ("не знаю", None)],
)
def test_classifier_answer_parsing(answer: str, tier: Tier | None) -> None:
    assert parse_classifier_answer(answer) is tier


def test_cost_uses_per_million_prices() -> None:
    ref = ModelRef.parse("anthropic/claude-opus-5-5")
    cost = cost_usd(DEFAULT_PRICING, ref, Usage(input_tokens=2_000, output_tokens=500))
    assert cost == Decimal("0.018000")  # 2k x $4/M + 500 x $20/M


def test_unknown_model_costs_zero() -> None:
    ref = ModelRef.parse("groq/some-new-model")
    assert cost_usd(DEFAULT_PRICING, ref, Usage(input_tokens=10, output_tokens=10)) == 0


def test_model_ref_splits_on_first_slash_only() -> None:
    ref = ModelRef.parse("groq/openai/gpt-oss-120b")
    assert (ref.provider, ref.model) == ("groq", "openai/gpt-oss-120b")
    with pytest.raises(ValueError, match="provider/model"):
        ModelRef.parse("gpt-oss")
