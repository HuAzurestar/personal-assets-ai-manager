"""Provider-reported counters and optional SDK cost; unknown is never zero."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from backend.middleware.provider import _field


@dataclass(frozen=True)
class Usage:
    input_tokens: int = -1
    output_tokens: int = -1
    total_tokens: int = -1
    cached_tokens: int = -1
    cost_usd: str = ""


def extract_usage(response: object) -> Usage:
    raw = _field(response, "usage")

    def count(value):
        return value if type(value) is int and 0 <= value <= 2**63 - 1 else -1

    input_tokens = count(_field(raw, "prompt_tokens"))
    output_tokens = count(_field(raw, "completion_tokens"))
    total_tokens = count(_field(raw, "total_tokens"))
    if total_tokens == -1 and input_tokens >= 0 and output_tokens >= 0:
        total_tokens = count(input_tokens + output_tokens)
    cached_tokens = count(_field(_field(raw, "prompt_tokens_details"), "cached_tokens"))
    cost = _field(_field(response, "_hidden_params"), "response_cost")
    cost_usd = ""
    if isinstance(cost, (int, float, str, Decimal)) and not isinstance(cost, bool):
        try:
            value = Decimal(str(cost))
            if value.is_finite() and 0 <= value <= Decimal("1000000000"):
                cost_usd = format(value.quantize(Decimal("0.000000000001")), "f")
        except (InvalidOperation, ValueError):
            pass
    return Usage(input_tokens, output_tokens, total_tokens, cached_tokens, cost_usd)
