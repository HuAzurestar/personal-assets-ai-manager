"""R18: reject unrepresentable IDs before any SQLite call or publication."""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from backend.schema.review_command import NonnegativeId, PositiveId
from backend.target_main import app


MAX_ID = 2**63 - 1


@pytest.mark.parametrize("alias,minimum", [(PositiveId, 1), (NonnegativeId, 0)])
def test_shared_identifier_accepts_exact_sqlite_bounds(alias, minimum):
    adapter = TypeAdapter(alias)
    assert adapter.validate_python(minimum) == minimum
    assert adapter.validate_python(MAX_ID) == MAX_ID
    for value in (MAX_ID + 1, -1, True, False, 1.0, "1"):
        with pytest.raises(ValidationError):
            adapter.validate_python(value)


@pytest.mark.parametrize("value", [MAX_ID + 1, -1, True, 1.0, "1"])
def test_public_write_ids_fail_validation_not_internal_error(value):
    requests = [
        ("/paam/ledger/v1/account-ref", dict(account_id=value)),
        ("/paam/ledger/v1/account", dict(name="Mock", party_id=value)),
        ("/paam/ledger/v1/review/preview", dict(new_reviews=[dict(
            case_code="NORMAL", parameters=dict(transaction_ids=[value]))])),
        ("/paam/financial/v1/position", dict(title="Mock", type="ASSET",
            usage_scenario="GENERAL", party_id=value, unit_code="CNY")),
    ]
    with TestClient(app) as client:
        for url, payload in requests:
            response = client.post(url, json=payload)
            assert response.status_code == 422, (url, response.text)


@pytest.mark.parametrize("value", [MAX_ID + 1, 0, -1])
def test_public_resource_paths_have_sqlite_bounds(value):
    paths = [
        f"/paam/ledger/v1/{kind}/{value}" for kind in
        ("account-party", "account", "account-ref")
    ] + [
        f"/paam/financial/v1/position/{value}",
        f"/paam/financial/v1/position/{value}/leg/list",
        f"/paam/import/v1/import_file/{value}",
        f"/paam/import/v1/import_file/1/row/{value}",
    ]
    with TestClient(app) as client:
        for path in paths:
            response = client.get(path)
            assert response.status_code == 422, (path, response.text)


@pytest.mark.parametrize("kind,key", [("account-party", "id"),
    ("account", "party_id"), ("account-ref", "account_id")])
def test_metadata_filters_reject_oversized_identifier(kind, key):
    with TestClient(app) as client:
        response = client.get(f"/paam/ledger/v1/{kind}/list", params={
            "filter": json.dumps(dict(key=key, op="=", val=MAX_ID + 1))})
    assert response.status_code == 422, response.text
    assert response.json()["body"]["code"] == "LIST_FILTER_VALUE_INVALID"


def test_position_owner_filter_rejects_oversized_identifier():
    with TestClient(app) as client:
        response = client.get("/paam/financial/v1/position/list", params={
            "filter": json.dumps(dict(key="party_id", op="=", val=MAX_ID + 1))})
    assert response.status_code == 422, response.text
