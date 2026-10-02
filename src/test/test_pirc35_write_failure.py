"""R22: real lock contention is known-not-committed across public writers."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.core import target_database
from backend.entity import LedgerAccountParty, LedgerEntry, Position, ReviewCase, TransactionFact
from backend.mapper.review_command_mapper import ReviewCommandMapper
from backend.target_main import app


@pytest.mark.parametrize("writer", ["review", "account", "position"])
def test_real_write_busy_is_uniform_and_has_no_partial_changes(writer):
    with TestClient(app) as client:
        with target_database.SessionLocal() as db:
            db.add(LedgerAccountParty(id=1, name="Mock person", status="ACTIVE"))
            db.add(TransactionFact(id=1, fact_key="mock-lock", amount=1000,
                currency_code="CNY", cash_direction=2, account_code="",
                occurred_time=datetime(2024, 1, 1, tzinfo=timezone.utc)))
            db.flush()
            ReviewCommandMapper(db).create_initial_defaults([1])
            db.commit()
        intent = dict(new_reviews=[dict(case_code="NORMAL", parameters=dict(transaction_ids=[1]))])
        response = client.post("/paam/ledger/v1/review/preview", json=intent)
        assert response.status_code == 200, response.text
        plan = response.json()["body"]
        url, payload = {
            "review": ("/paam/ledger/v1/review/command", intent | dict(
                expected_reviews=plan["expected_reviews"], preview_digest=plan["preview_digest"])),
            "account": ("/paam/ledger/v1/account-party", dict(name="Mock blocked person")),
            "position": ("/paam/financial/v1/position", dict(title="Mock blocked object",
                type="ASSET", usage_scenario="GENERAL", party_id=1, unit_code="CNY")),
        }[writer]
        with target_database.engine.connect() as holder:
            holder.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                response = client.post(url, json=payload)
            finally:
                holder.rollback()
        assert response.status_code == 503, response.text
        assert response.json()["body"]["code"] == "WRITE_BUSY"
        with target_database.SessionLocal() as db:
            counts = [db.scalar(select(func.count()).select_from(entity)) for entity in
                (LedgerAccountParty, TransactionFact, ReviewCase, LedgerEntry, Position)]
            assert counts == [1, 1, 1, 1, 0]
            assert db.get(ReviewCase, 1).status == 0
