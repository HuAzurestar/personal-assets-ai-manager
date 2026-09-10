"""Exact CNY arithmetic at the write boundary; never silently round facts."""
from decimal import Decimal, InvalidOperation


def cents(value) -> int:
    try:
        amount = Decimal(str(value))
        scaled = amount * 100
        if not amount.is_finite() or scaled != scaled.to_integral_value():
            raise ValueError("金额必须为有限数值，且最多两位小数")
        if abs(scaled) > 9_000_000_000_000:
            raise ValueError("金额超出支持范围")
        return int(scaled)
    except InvalidOperation as error:
        raise ValueError("金额无效") from error


def money(value: int) -> float:
    return float(Decimal(value) / 100)
