from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.core import target_database
from backend.entity import LedgerEntry, ReviewCase, ReviewRevision, TransactionFact
from backend.mapper.review_command_mapper import ReviewCommandMapper
from backend.target_main import app


def test_public_review_preview_command_and_retired_write_paths():
    with TestClient(app) as client:
        with target_database.SessionLocal() as db:
            db.add(TransactionFact(id=1, fact_key="fictional", amount=1000, currency_code="CNY",
                cash_direction=2, account_code="", occurred_time=datetime(2024, 1, 1, tzinfo=timezone.utc)))
            db.flush()
            ReviewCommandMapper(db).create_initial_defaults([1])
            db.commit()
        intent = dict(new_reviews=[dict(case_code="NORMAL", title="Mock corrected", parameters=dict(transaction_ids=[1]))])
        response = client.post("/paam/ledger/v1/review/preview", json=intent)
        assert response.status_code == 200, response.text
        preview = response.json()["body"]
        assert preview["blocking_issues"] == []
        assert preview["new_reviews"][0]["type"] == "OTHER_MANUAL"
        assert "entry_type" not in preview["new_reviews"][0]["allocations"][0]
        command = intent | dict(preview_digest=preview["preview_digest"], expected_reviews=preview["expected_reviews"])
        response = client.post("/paam/ledger/v1/review/command", json=command)
        assert response.status_code == 200, response.text
        review = response.json()["body"]["created_reviews"][0]
        assert review["type"] == "OTHER_MANUAL"
        detail = client.get(f"/paam/ledger/v1/review/{review['id']}")
        assert detail.status_code == 200, detail.text
        body = detail.json()["body"]
        assert {"history", "version", "behavior_type"}.isdisjoint(body)
        assert body["ledger_entries"][0]["economic_type"] == "TRANSACTION"
        assert body["ledger_entries"][0]["cash_direction"] == "OUT"
        assert body["allocations"][0]["transaction_id"] == 1
        assert client.post("/paam/ledger/v1/review/command", json=command).status_code == 409
        for path, payload in [("/review", {}), (f"/review/{review['id']}/revoke", {"idempotency_key": "old"}),
                              (f"/review/{review['id']}/restore", {"idempotency_key": "old"}),
                              (f"/flow/{body['ledger_entries'][0]['id']}/account", {"account_code": "changed"})]:
            response = (client.put if "/account" in path else client.post)("/paam/ledger/v1" + path, json=payload)
            assert response.status_code == 410, response.text
        with target_database.SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(ReviewCase)) == 2
            assert db.scalar(select(func.count()).select_from(LedgerEntry)) == 2
            assert db.scalar(select(func.count()).select_from(ReviewRevision)) == 0


def test_new_openapi_does_not_accept_published_cash_rows():
    schemas = app.openapi()["components"]["schemas"]
    assert set(schemas["MoneySplitInput"]["properties"]) == {"transaction_id", "economic_type", "cash_amount", "account_ref_id"}
    assert schemas["ReviewCommandInput"]["properties"]["new_reviews"]["items"]["discriminator"]["propertyName"] == "case_code"
    assert schemas["ReviewPO"]["properties"]["type"]["enum"] == ["NORMAL_TRANSACTION", "BORROW_AND_REPAY", "CREDIT_CARD", "SHARED_SETTLEMENT", "OTHER_MANUAL"]
