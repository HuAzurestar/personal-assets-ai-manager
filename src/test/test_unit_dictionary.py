"""Code-owned unit catalog: exact, complete, read-only and public."""
from decimal import Decimal

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event

from backend.core import target_database
from backend.core.money import currency_precision, DEFAULT_CURRENCY_PRECISION
from backend.core.unit import SUPPORTED_UNIT_CODES, QUANTITY_UNITS, unit_definition
from backend.target_main import app

URL = '/paam/ledger/v1/unit'


def test_complete_catalog_matches_authoritative_definitions_without_sql():
    statements = []
    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    # Exclude application startup; the dictionary GET itself needs no SQL.
    event.listen(target_database.engine, 'before_cursor_execute', record)
    try:
        with TestClient(app) as client:
            statements.clear()
            response = client.get(URL)
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload['status'] == 200 and payload['message'] == 'ok'
            assert set(payload['body']) == {'items'}
            items = payload['body']['items']
            assert {row['code'] for row in items} == SUPPORTED_UNIT_CODES
            assert len(items) == len(SUPPORTED_UNIT_CODES) == 72
            assert statements == []
            for row in items:
                definition = unit_definition(row['code'])
                assert row['dimension'] == definition.dimension
                assert row['quantum'] == definition.quantum
                assert Decimal(row['quantum']) == Decimal(1).scaleb(-row['precision'])
                assert row['is_default'] == (row['code'] in DEFAULT_CURRENCY_PRECISION)
                if row['dimension'] == 'CURRENCY':
                    assert row['precision'] == currency_precision(row['code'])
                else:
                    assert row['code'] in QUANTITY_UNITS
            defaults = [row['code'] for row in items if row['is_default']]
            assert defaults == sorted(DEFAULT_CURRENCY_PRECISION)
            assert len(response.content) < 32768
    finally:
        event.remove(target_database.engine, 'before_cursor_execute', record)


@pytest.mark.parametrize('params', [{'page_size': 20}, {'query': '[]'}, {'filter': '{}'}, {'sorter': '[]'}, {'raw': 1}])
def test_catalog_is_not_a_silently_ignored_list_query(params):
    with TestClient(app) as client:
        response = client.get(URL, params=params)
        assert response.status_code == 422, response.text
        assert response.json()['body']['code'] == 'LIST_PARAMETER_NOT_SUPPORTED'


def test_catalog_is_read_only_and_documented():
    with TestClient(app) as client:
        assert client.post(URL, json={}).status_code == 405
        assert client.put(URL, json={}).status_code == 405
        specification = client.get('/openapi.json').json()
        assert set(specification['paths'][URL]) == {'get'}
        assert 'UnitDictionaryResponse' in specification['components']['schemas']


def test_catalog_capacity_failure_is_explicit_not_a_partial_dictionary(monkeypatch):
    import backend.service.unit_dictionary_service as service
    with TestClient(app) as client:
        monkeypatch.setattr(service, 'MAX_DICTIONARY_UNITS', 1)
        response = client.get(URL)
        assert response.status_code == 413, response.text
        assert response.json()['body']['code'] == 'UNIT_DICTIONARY_LIMIT'


def test_catalog_byte_capacity_failure_is_explicit(monkeypatch):
    import backend.service.unit_dictionary_service as service
    with TestClient(app) as client:
        monkeypatch.setattr(service, 'MAX_DICTIONARY_BYTES', 1)
        response = client.get(URL)
        assert response.status_code == 413, response.text
        assert response.json()['body']['code'] == 'UNIT_DICTIONARY_LIMIT'
