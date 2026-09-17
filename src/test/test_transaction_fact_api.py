import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.target_database import init_target_db
from backend.entity import (
    CASH_DIRECTION_IN,
    CASH_DIRECTION_OUT,
    IMPORT_FILE_FORMAT_CSV,
    IMPORT_FILE_STATUS_IMPORTED,
    IMPORT_ROW_STATUS_ACCEPTED,
    IMPORT_SOURCE_ALIPAY,
    TransactionFact,
    TransactionImportFile,
    TransactionImportRow,
)
from backend.mapper.target_economic_mapper import TargetEconomicMapper
from backend.router.dependency import get_db
from backend.router.ledger_transaction_fact import router
from backend.service.target_economic_service import TargetEconomicService


@pytest.fixture
def transaction_fact_api(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'transaction-fact-api.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False)
    init_target_db(bind=engine)
    api = FastAPI()
    api.include_router(router)

    def override_db():
        with sessions() as db:
            yield db

    api.dependency_overrides[get_db] = override_db
    with TestClient(api) as client:
        yield client, sessions
    engine.dispose()


def _seed(sessions):
    now = datetime(2026, 9, 16, 9)
    with sessions() as db:
        imported = TransactionImportFile(
            batch_code="batch-1",
            source_type=IMPORT_SOURCE_ALIPAY,
            filename="september.csv",
            file_format=IMPORT_FILE_FORMAT_CSV,
            sha256="a" * 64,
            period_start="2026-09-01",
            period_end="2026-09-30",
            total_count=2,
            success_count=2,
            skip_count=0,
            issue_count=0,
            status=IMPORT_FILE_STATUS_IMPORTED,
            created_time=now,
            updated_time=now,
        )
        facts = [
            TransactionFact(
                fact_key=f"fact-{index}",
                occurred_time=now + timedelta(days=index),
                cash_direction={"IN": CASH_DIRECTION_IN, "OUT": CASH_DIRECTION_OUT}[direction],
                amount_value=amount,
                amount_scale=2,
                currency_code=currency,
                account_code=f"account-{index}",
                counterparty_name=counterparty,
                counterparty_account_ref="",
                summary=summary,
                created_time=now,
                updated_time=now,
            )
            for index, direction, amount, currency, counterparty, summary in (
                (1, "OUT", 1200, "CNY", "Coffee", "Breakfast"),
                (2, "IN", 3400, "USD", "Client", "Consulting"),
            )
        ]
        db.add(imported)
        db.add_all(facts)
        db.flush()
        db.add_all([
            TransactionImportRow(
                transaction_fact_id=fact.id,
                transaction_import_file_id=imported.id,
                source_row_number=index,
                source_reference=f"source-{index}",
                raw_payload="{}",
                raw_hash=str(index) * 64,
                row_status=IMPORT_ROW_STATUS_ACCEPTED,
                issue_code="",
                issue_message="",
                created_time=now,
                updated_time=now,
            )
            for index, fact in enumerate(facts, 1)
        ])
        db.commit()
        fact_ids = [fact.id for fact in facts]
    with sessions() as db:
        TargetEconomicService(db).ensure_defaults(fact_ids, commit=True)
    return fact_ids


def test_transaction_fact_list_is_a_pure_server_queried_po_list(
    transaction_fact_api,
):
    client, sessions = transaction_fact_api
    _seed(sessions)

    response = client.get(
        "/paam/ledger/v1/transaction_fact/list",
        params={
            "page_index": 1,
            "page_size": 20,
            "filter": json.dumps({
                "op": "AND",
                "expression": [
                    {"key": "cash_direction", "op": "=", "val": "IN"},
                    {"key": "currency_code", "op": "=", "val": "usd"},
                    {
                        "key": "occurred_time",
                        "op": "between",
                        "val": {
                            "start": datetime(2026, 9, 17, tzinfo=timezone.utc).isoformat(),
                            "end": datetime(2026, 9, 19, tzinfo=timezone.utc).isoformat(),
                        },
                    },
                ],
            }),
            "sorter": json.dumps([
                {"key": "amount_value", "direction": "asc"},
            ]),
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == 200
    assert payload["message"] == "ok"
    assert payload["warnings"][0]["code"] == "LIST_AMOUNT_SORT_GROUPED"
    body = payload["body"]
    assert (body["total"], body["page_index"], body["page_size"]) == (1, 1, 20)
    assert set(body) == {"items", "total", "page_index", "page_size"}
    assert body["items"][0]["summary"] == "Consulting"
    assert "available_value" not in body["items"][0]
    assert "fact_key" not in body["items"][0]


def test_transaction_fact_detail_follows_allocation_relationships(
    transaction_fact_api,
):
    client, sessions = transaction_fact_api
    fact_id = _seed(sessions)[0]

    response = client.get(f"/paam/ledger/v1/transaction_fact/{fact_id}")

    assert response.status_code == 200, response.text
    body = response.json()["body"]
    assert body["transaction_fact"]["id"] == fact_id
    assert body["transaction_fact"]["fact_key"] == "fact-1"
    assert body["import_evidence"][0]["filename"] == "september.csv"
    allocation = body["allocations"][0]
    assert allocation["fact_id"] == fact_id
    assert allocation["review_id"] == body["reviews"][0]["id"]
    assert allocation["economic_id"] == body["ledgers"][0]["id"]

    with sessions() as db:
        mapper = TargetEconomicMapper(db)
        assert mapper.allocations_by_relation(
            review_ids=[allocation["review_id"]],
            fact_ids=[fact_id],
            economic_ids=[allocation["economic_id"]],
        )[0]["id"] == allocation["id"]


def test_transaction_fact_query_rejects_unknown_fields(transaction_fact_api):
    client, _sessions = transaction_fact_api

    response = client.get(
        "/paam/ledger/v1/transaction_fact/list",
        params={
            "sorter": '[{"key":"drop_table","direction":"asc"}]',
        },
    )

    assert response.status_code == 422
    assert response.json()["status"] == 422
    assert response.json()["body"]["code"] == "LIST_SORTER_FIELD_NOT_SUPPORTED"


def test_transaction_fact_list_allows_minimal_request_and_rejects_query(
    transaction_fact_api,
):
    client, sessions = transaction_fact_api
    _seed(sessions)

    minimal = client.get(
        "/paam/ledger/v1/transaction_fact/list",
        params={"page_index": 1, "page_size": 10},
    )
    assert minimal.status_code == 200, minimal.text
    assert minimal.json()["body"]["total"] == 2
    assert "warnings" not in minimal.json()

    rejected = client.get(
        "/paam/ledger/v1/transaction_fact/list",
        params={"query": '[{"key":"summary","word":"Coffee"}]'},
    )
    assert rejected.status_code == 422
    assert rejected.json()["body"]["code"] == "LIST_QUERY_NOT_SUPPORTED"

    legacy = client.get(
        "/paam/ledger/v1/transaction_fact/list",
        params={"page": 1, "q": "Coffee"},
    )
    assert legacy.status_code == 422
    assert legacy.json()["body"]["code"] == "LIST_PARAMETER_NOT_SUPPORTED"
    assert legacy.json()["body"]["details"]["parameters"] == ["page", "q"]


def test_transaction_fact_amount_sort_omits_warning_when_money_is_constrained(
    transaction_fact_api,
):
    client, sessions = transaction_fact_api
    _seed(sessions)

    response = client.get(
        "/paam/ledger/v1/transaction_fact/list",
        params={
            "filter": json.dumps({
                "op": "AND",
                "expression": [
                    {"key": "currency_code", "op": "=", "val": "CNY"},
                    {"key": "amount_scale", "op": "=", "val": 2},
                ],
            }),
            "sorter": '[{"key":"amount_value","direction":"desc"}]',
        },
    )

    assert response.status_code == 200, response.text
    assert "warnings" not in response.json()
    assert response.json()["body"]["total"] == 1


def test_transaction_fact_openapi_exposes_only_canonical_list_parameters(
    transaction_fact_api,
):
    client, _sessions = transaction_fact_api

    operation = client.get("/openapi.json").json()["paths"][
        "/paam/ledger/v1/transaction_fact/list"
    ]["get"]
    assert [parameter["name"] for parameter in operation["parameters"]] == [
        "page_index",
        "page_size",
        "query",
        "filter",
        "sorter",
    ]
    schema = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert schema["$ref"].endswith("/TransactionFactListResponse")


def test_transaction_fact_detail_returns_shared_not_found_error(
    transaction_fact_api,
):
    client, _sessions = transaction_fact_api

    response = client.get("/paam/ledger/v1/transaction_fact/999")

    assert response.status_code == 404
    assert response.json()["status"] == 404
    assert response.json()["body"]["code"] == "FACT_ERROR"
