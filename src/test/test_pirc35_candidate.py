from datetime import datetime, timezone
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, update
from backend.core import target_database
from backend.entity import TransactionFact, LedgerAccountParty, LedgerAccount, LedgerAccountRef, ReviewCase
from backend.mapper.review_command_mapper import ReviewCommandMapper
from backend.target_main import app


@pytest.fixture
def client():
    with TestClient(app) as client:
        with target_database.SessionLocal() as db:
            db.add(LedgerAccountParty(id=1, name="Mock person"))
            db.add(LedgerAccount(id=1, party_id=1, name="Mock group"))
            db.add_all([LedgerAccountRef(id=1, account_id=1), LedgerAccountRef(id=2, account_id=0)])
            db.add_all([TransactionFact(id=i, fact_key=f"mock-{i}", cash_direction=2, amount=1000,
                currency_code="CNY", account_code="IMMUTABLE SOURCE", summary="No match" if i == 1 else "Target",
                counterparty_name="Mock counterpart", occurred_time=datetime(2024, 1, i, tzinfo=timezone.utc)) for i in range(1, 4)])
            db.flush()
            ReviewCommandMapper(db).create_initial_defaults([1, 2, 3], account_refs={1: 1, 2: 2})
            db.commit()
        yield client


def page(client, **parameters):
    response = client.get("/paam/ledger/v1/candidate/list", params=parameters)
    assert response.status_code == 200, response.text
    return response.json()["body"]


def test_all_facts_still_candidates_after_manual_review_and_unique_default_unchanged(client):
    initial = page(client, page_size=1)
    assert initial["total"] == 3 and set(initial) == {"items", "total", "page_index", "page_size"}
    intent = dict(new_reviews=[dict(case_code="NORMAL", parameters=dict(transaction_ids=[1, 2]))])
    preview = client.post("/paam/ledger/v1/review/preview", json=intent).json()["body"]
    response = client.post("/paam/ledger/v1/review/command", json=intent | dict(
        expected_reviews=preview["expected_reviews"], preview_digest=preview["preview_digest"]))
    assert response.status_code == 200, response.text
    result = page(client)
    assert result["total"] == 3
    for item in result["items"]:
        assert item["default_review"]["review_id"] == item["transaction_id"]
        assert item["coverage"]["state"] == "FULL"
        assert item["coverage"]["allocated_cash_amount"] == item["cash_amount"] == 1000
        assert item["cash_direction"] == "OUT" and "account_code" not in item


@pytest.mark.parametrize("dimension,value,expected", [("account_ref_id", 1, {1}), ("account_ref_id", 0, {3}),
    ("account_id", 1, {1}), ("account_id", 0, {2}), ("party_id", 1, {1}), ("party_id", 99, set())])
def test_current_ownership_filter_known_unassigned_distinct_from_unknown(client, dimension, value, expected):
    items = page(client, filter=json.dumps(dict(key=dimension, op="=", val=value)))["items"]
    assert {item["transaction_id"] for item in items} == expected
    with target_database.SessionLocal() as db:
        db.execute(update(LedgerAccountRef).where(LedgerAccountRef.id == 2).values(account_id=1, status="CLOSED"))
        db.commit()
    assert {item["transaction_id"] for item in page(client, filter='{"key":"party_id","op":"=","val":1}')["items"]} == {1, 2}


def test_candidate_search_scans_once_per_batch_and_preserves_empty_hit_cursor(client):
    params = dict(page_size=1, sorter='[{"key":"id","direction":"asc"}]', query='[{"key":"summary","word":"Target"}]')
    first = client.get("/paam/ledger/v1/candidate/search", params=params).json()["body"]
    assert first["items"] == [] and first["has_more"] and first["total"] is None and first["scanned_count"] == 1
    second = client.get("/paam/ledger/v1/candidate/search", params=params | dict(cursor=first["next_cursor"])).json()["body"]
    assert second["items"][0]["transaction_id"] == 2
    assert second["items"][0]["coverage"]["allocated_cash_amount"] == 1000


def test_candidate_missing_original_default_is_disclosed_never_repaired(client):
    with target_database.SessionLocal() as db:
        db.add(TransactionFact(id=4, fact_key="missing-default", cash_direction=2, amount=1000, currency_code="CNY",
            account_code="", occurred_time=datetime(2024, 1, 1, tzinfo=timezone.utc)))
        db.commit()
    item = page(client, filter='{"key":"id","op":"=","val":4}')["items"][0]
    assert item["default_review"] is None
    assert item["coverage"]["default_identity_state"] == "MISSING" and item["coverage"]["state"] == "UNRESOLVED"
    with target_database.SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ReviewCase)) == 3


def test_candidate_time_between_is_left_closed_right_open_and_no_account_sort(client):
    result = page(client, filter=json.dumps(dict(key="occurred_time", op="between",
        val=dict(start="2024-01-01T00:00:00Z", end="2024-01-03T00:00:00Z"))))
    assert {item["transaction_id"] for item in result["items"]} == {1, 2}
    response = client.get("/paam/ledger/v1/candidate/list", params=dict(sorter='[{"key":"account_ref_id","direction":"asc"}]'))
    assert response.status_code == 422
