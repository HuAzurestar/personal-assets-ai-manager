from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.target_database import init_target_db
from backend.entity import BillFact, BillRaw, ImportFile
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
        imported = ImportFile(
            batch_code="batch-1",
            source_type="ALIPAY",
            institution_code="ALIPAY",
            filename="september.csv",
            file_format="CSV",
            sha256="a" * 64,
            period_start="2026-09-01",
            period_end="2026-09-30",
            total_count=2,
            success_count=2,
            skip_count=0,
            issue_count=0,
            status="CONFIRMED",
            created_time=now,
            updated_time=now,
        )
        facts = [
            BillFact(
                fact_key=f"fact-{index}",
                occurred_time=now + timedelta(days=index),
                cash_direction=direction,
                amount_value=amount,
                amount_scale=2,
                currency_code=currency,
                account_code=f"account-{index}",
                counterparty=counterparty,
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
            BillRaw(
                bill_id=fact.id,
                import_file_id=imported.id,
                source_row_number=index,
                source_reference=f"source-{index}",
                raw_payload="{}",
                raw_hash=str(index) * 64,
                parse_status="ACCEPTED",
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
            "page": 1,
            "page_size": 20,
            "q": "client",
            "filter": '{"cash_direction":"IN","currency_code":"usd"}',
            "sorter": '{"field":"amount_value","order":"asc"}',
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()["body"]
    assert (body["total"], body["page"], body["page_size"]) == (1, 1, 20)
    assert body["filter"] == {
        "cash_direction": "IN",
        "currency_code": "usd",
        "account_code": None,
        "date_from": None,
        "date_to": None,
    }
    assert body["sorter"] == {"field": "amount_value", "order": "asc"}
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
        params={"sorter": '{"field":"drop_table","order":"asc"}'},
    )

    assert response.status_code == 422
    assert response.json()["status"] == 422
    assert response.json()["body"]["code"] == "LIST_QUERY_ERROR"


def test_transaction_fact_detail_returns_shared_not_found_error(
    transaction_fact_api,
):
    client, _sessions = transaction_fact_api

    response = client.get("/paam/ledger/v1/transaction_fact/999")

    assert response.status_code == 404
    assert response.json()["status"] == 404
    assert response.json()["body"]["code"] == "FACT_ERROR"
