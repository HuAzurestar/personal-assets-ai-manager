import pytest

from backend.core.unit import quantity_from_decimal, unit_definition


def test_exact_units_reuse_currency_quantum():
    assert unit_definition("CNY").quantum == "0.01"
    assert quantity_from_decimal("0.001", "KG_3") == 1
    assert quantity_from_decimal("1", "PCS") == 1
    assert quantity_from_decimal("400.00", "CNY") == 40000


@pytest.mark.parametrize("value,code", [(True, "PCS"), ("NaN", "KG_3"), ("Infinity", "CNY"),
                                       ("1.1", "PCS"), ("0.0001", "KG_3"), ("0", "CNY"),
                                       ("1", "UNKNOWN"), ("9000000000001", "PCS")])
def test_unknown_or_inexact_quantities_are_rejected(value, code):
    with pytest.raises(ValueError):
        quantity_from_decimal(value, code)
