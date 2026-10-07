from decimal import Decimal

from brand_api.llm.config import ModelPrice
from brand_api.llm.types import ModelRef, Usage
from brand_api.logging_setup import get_logger

log = get_logger(__name__)
_PER_MILLION = Decimal(1_000_000)
_warned: set[str] = set()


def cost_usd(pricing: dict[str, ModelPrice], ref: ModelRef, usage: Usage) -> Decimal:
    price = pricing.get(str(ref))
    if price is None:
        if str(ref) not in _warned:
            _warned.add(str(ref))
            log.warning("model_price_missing", model=str(ref))
        return Decimal(0)
    total = (
        Decimal(usage.input_tokens) * Decimal(str(price.input))
        + Decimal(usage.output_tokens) * Decimal(str(price.output))
    ) / _PER_MILLION
    return total.quantize(Decimal("0.000001"))
