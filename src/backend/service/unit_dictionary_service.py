"""Publish the existing exact unit registry without a table or SQL query."""
from decimal import Decimal

from backend.core.money import DEFAULT_CURRENCY_PRECISION
from backend.core.unit import SUPPORTED_UNIT_CODES, unit_definition
from backend.error import TargetEconomicError
from backend.schema.unit_dictionary import UnitDefinitionPO, UnitDictionaryPO

MAX_DICTIONARY_UNITS = 128
MAX_DICTIONARY_BYTES = 32768


class UnitDictionaryService:
    def get(self) -> UnitDictionaryPO:
        if len(SUPPORTED_UNIT_CODES) > MAX_DICTIONARY_UNITS:
            raise TargetEconomicError(413, "Unit dictionary exceeds capacity", code="UNIT_DICTIONARY_LIMIT")
        definitions = [unit_definition(code) for code in SUPPORTED_UNIT_CODES]
        definitions.sort(key=lambda unit: (
            0 if unit.code in DEFAULT_CURRENCY_PRECISION else 1 if unit.dimension == "CURRENCY" else 2,
            unit.code,
        ))
        body = UnitDictionaryPO(items=[UnitDefinitionPO(
            code=unit.code, label=unit.label, dimension=unit.dimension, quantum=unit.quantum,
            precision=-Decimal(unit.quantum).as_tuple().exponent,
            is_default=unit.code in DEFAULT_CURRENCY_PRECISION,
        ) for unit in definitions])
        if len(body.model_dump_json().encode("utf-8")) > MAX_DICTIONARY_BYTES:
            raise TargetEconomicError(413, "Unit dictionary exceeds capacity", code="UNIT_DICTIONARY_LIMIT")
        return body
