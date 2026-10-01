"""Code-owned exact units shared by money and Position quantity."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from backend.core.money import MAX_ABS_AMOUNT, currency_quantum, normalize_currency_code


@dataclass(frozen=True, slots=True)
class UnitDefinition:
    code: str
    label: str
    dimension: str
    quantum: str


QUANTITY_UNITS = {
    "KG_3": UnitDefinition("KG_3", "千克", "MASS", "0.001"),
    "PCS": UnitDefinition("PCS", "件", "COUNT", "1"),
}


def unit_definition(code: str) -> UnitDefinition:
    if code in QUANTITY_UNITS:
        return QUANTITY_UNITS[code]
    currency = normalize_currency_code(code)
    return UnitDefinition(currency, currency, "CURRENCY", str(currency_quantum(currency)))


def quantity_from_decimal(value: object, code: str) -> int:
    if isinstance(value, bool):
        raise ValueError("INVALID_QUANTITY")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("INVALID_QUANTITY") from error
    if not amount.is_finite():
        raise ValueError("INVALID_QUANTITY")
    scaled = amount / Decimal(unit_definition(code).quantum)
    if scaled != scaled.to_integral_value() or scaled <= 0 or scaled > MAX_ABS_AMOUNT:
        raise ValueError("QUANTITY_PRECISION_OR_RANGE")
    return int(scaled)
