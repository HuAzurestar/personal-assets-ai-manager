from decimal import Decimal

import pytest

from backend.core.money import (
    amount_from_decimal,
    currency_precision,
    currency_quantum,
    decimal_from_amount,
    normalize_currency_code,
)


def test_base_currency_uses_registered_default_precision():
    assert normalize_currency_code(" cny ") == "CNY"
    assert currency_precision("CNY") == 2
    assert currency_quantum("CNY") == Decimal("0.01")
    assert amount_from_decimal("5001.23", "CNY") == 500123
    assert decimal_from_amount(500123, "CNY") == Decimal("5001.23")


def test_precision_suffix_defines_a_distinct_exact_unit():
    assert currency_precision("CNY_4") == 4
    assert currency_quantum("CNY_4") == Decimal("0.0001")
    assert amount_from_decimal("50.0123", "CNY_4") == 500123
    assert decimal_from_amount(500123, "CNY_4") == Decimal("50.0123")


def test_currency_conversion_rejects_rounding_and_unsupported_codes():
    with pytest.raises(ValueError, match="精确表示"):
        amount_from_decimal("1.001", "CNY")
    with pytest.raises(ValueError, match="不支持的币种单位"):
        amount_from_decimal("1.00", "UNKNOWN")


def test_currency_conversion_never_returns_float():
    value = decimal_from_amount(123, "USD")
    assert value == Decimal("1.23")
    assert isinstance(value, Decimal)
