from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.core.target_database import init_target_db
from backend.entity import (
    CASH_DIRECTION_IN,
    CASH_DIRECTION_OUT,
    ReviewAllocation,
    ReviewCase,
    TransactionFact,
)
from backend.router.dependency import get_db
from backend.router.ledger import router as ledger_router
from backend.router.ledger_account import router as ledger_account_router
from backend.service.target_economic_service import TargetEconomicService


@pytest.fixture
def target_account_api(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'target-account.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False)
    init_target_db(bind=engine)
    api = FastAPI()
    api.include_router(ledger_account_router)
    api.include_router(ledger_router)

    def override_db():
        with sessions() as db:
            yield db

    api.dependency_overrides[get_db] = override_db
    with TestClient(api) as client:
        yield client, sessions
    engine.dispose()


def _facts(sessions, specifications):
    now = datetime(2026, 9, 12, 12)
    with sessions() as db:
        facts = []
        for index, (direction, account) in enumerate(specifications):
            fact = TransactionFact(
                fact_key=uuid4().hex,
                occurred_time=now + timedelta(minutes=index),
                cash_direction={"IN": CASH_DIRECTION_IN, "OUT": CASH_DIRECTION_OUT}[direction],
                amount=1000,
                amount_scale=2,
                currency_code="CNY",
                account_code=account,
                counterparty_name="counterparty",
                counterparty_account_ref="",
                summary="transaction",
                created_time=now,
                updated_time=now,
            )
            db.add(fact)
            facts.append(fact)
        db.flush()
        fact_ids = [fact.id for fact in facts]
        TargetEconomicService(db).ensure_defaults(fact_ids, commit=True)
        return fact_ids


def _ledger_id(sessions, fact_id):
    with sessions() as db:
        return db.scalar(select(ReviewAllocation.ledger_entry_id).join(
            ReviewCase, ReviewCase.id == ReviewAllocation.review_case_id,
        ).where(
            ReviewAllocation.transaction_fact_id == fact_id,
            ReviewAllocation.ledger_entry_id > 0,
            ReviewCase.status == 0,
        ))


def test_ledger_account_is_independent_from_transaction_fact(target_account_api):
    client, sessions = target_account_api
    fact_id = _facts(sessions, [("OUT", "fact-wallet")])[0]
    ledger_id = _ledger_id(sessions, fact_id)

    account = client.get(f"/paam/ledger/v1/flow/{ledger_id}/account")
    assert account.status_code == 200, account.text
    assert account.json()["status"] == 200
    assert account.json()["body"]["account_code"] == "fact-wallet"

    updated = client.put(f"/paam/ledger/v1/flow/{ledger_id}/account", json={
        "account_code": "ledger-wallet",
        "expected_projection_version": account.json()["body"]["projection_version"],
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["body"]["account_code"] == "ledger-wallet"
    assert updated.json()["body"]["projection_version"] > account.json()["body"]["projection_version"]

    with sessions() as db:
        assert db.get(TransactionFact, fact_id).account_code == "fact-wallet"
    detail = client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()["body"]
    assert detail["flow"]["account_code"] == "ledger-wallet"
    assert detail["facts"][0]["account_code"] == "fact-wallet"


def test_ledger_account_rejects_stale_version_and_projection_sentinel(
    target_account_api,
):
    client, sessions = target_account_api
    fact_id = _facts(sessions, [("OUT", "wallet")])[0]
    ledger_id = _ledger_id(sessions, fact_id)

    invalid = client.put(f"/paam/ledger/v1/flow/{ledger_id}/account", json={
        "account_code": "MULTIPLE",
        "expected_projection_version": client.get(
            f"/paam/ledger/v1/flow/{ledger_id}/account"
        ).json()["body"]["projection_version"],
    })
    assert invalid.status_code == 422

    first = client.put(f"/paam/ledger/v1/flow/{ledger_id}/account", json={
        "account_code": "checked-wallet",
        "expected_projection_version": client.get(
            f"/paam/ledger/v1/flow/{ledger_id}/account"
        ).json()["body"]["projection_version"],
    })
    assert first.status_code == 200, first.text
    stale = client.put(f"/paam/ledger/v1/flow/{ledger_id}/account", json={
        "account_code": "stale-wallet",
        "expected_projection_version": 1,
    })
    assert stale.status_code == 409
    assert stale.json()["status"] == 409


def test_ledger_account_returns_not_found_for_unknown_ledger(target_account_api):
    client, _ = target_account_api
    response = client.get("/paam/ledger/v1/flow/999/account")
    assert response.status_code == 404
    assert response.json()["status"] == 404
