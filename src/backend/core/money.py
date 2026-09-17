"""Exact integer-money conversion for precision-bearing currency codes."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


DEFAULT_CURRENCY_PRECISION = {
    "CNY": 2,
    "EUR": 2,
    "GBP": 2,
    "HKD": 2,
    "JPY": 0,
    "USD": 2,
}
MAX_CURRENCY_PRECISION = 8
MAX_ABS_AMOUNT = 9_000_000_000_000

_CURRENCY_CODE = re.compile(
    rf"^(?P<base>[A-Z][A-Z0-9]{{1,11}}?)(?:_(?P<precision>[0-{MAX_CURRENCY_PRECISION}]))?$"
)


def normalize_currency_code(value: str) -> str:
    """Return the canonical supported currency-unit code."""

    code = str(value).strip().upper()
    match = _CURRENCY_CODE.fullmatch(code)
    if match is None or match.group("base") not in DEFAULT_CURRENCY_PRECISION:
        raise ValueError(f"不支持的币种单位：{value}")
    return code


def currency_precision(currency_code: str) -> int:
    """Resolve the decimal precision encoded by a currency-unit code."""

    code = normalize_currency_code(currency_code)
    match = _CURRENCY_CODE.fullmatch(code)
    assert match is not None
    explicit = match.group("precision")
    return (
        int(explicit)
        if explicit is not None
        else DEFAULT_CURRENCY_PRECISION[match.group("base")]
    )


def currency_quantum(currency_code: str) -> Decimal:
    """Return the real-world value represented by one stored integer unit."""

    return Decimal(1).scaleb(-currency_precision(currency_code))


def amount_from_decimal(value: object, currency_code: str) -> int:
    """Convert a decimal amount to its exact stored integer representation."""

    try:
        decimal_value = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("金额无效") from error
    if not decimal_value.is_finite():
        raise ValueError("金额必须为有限数值")
    scaled = decimal_value / currency_quantum(currency_code)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"金额无法由 {normalize_currency_code(currency_code)} 精确表示")
    amount = int(scaled)
    if abs(amount) > MAX_ABS_AMOUNT:
        raise ValueError("金额超出支持范围")
    return amount


def decimal_from_amount(amount: int, currency_code: str) -> Decimal:
    """Convert a stored integer amount to an exact Decimal display value."""

    if isinstance(amount, bool) or not isinstance(amount, int):
        raise ValueError("存储金额必须是整数")
    if abs(amount) > MAX_ABS_AMOUNT:
        raise ValueError("金额超出支持范围")
    return Decimal(amount) * currency_quantum(currency_code)
