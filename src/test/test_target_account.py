from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.router.dependency import get_db
from backend.router.ledger import router as ledger_router
from backend.router.ledger_account import router as ledger_account_router
from backend.router.ledger_account_legacy import router as ledger_account_legacy_router
from backend.router.ledger_review import router as ledger_review_router
from backend.core.target_database import init_target_db
from backend.entity import BillFact, ReviewCase, ReviewCaseBill, ReviewHistory
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
    api.include_router(ledger_account_legacy_router)
    api.include_router(ledger_review_router)
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
            fact = BillFact(
                fact_key=uuid4().hex,
                occurred_time=now + timedelta(minutes=index),
                cash_direction=direction,
                amount_value=1000,
                amount_scale=2,
                currency_code="CNY",
                account_code=account,
                counterparty="counterparty",
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
        return db.scalar(select(ReviewCaseBill.economic_id).join(
            ReviewCase, ReviewCase.id == ReviewCaseBill.case_id,
        ).where(
            ReviewCaseBill.bill_id == fact_id,
            ReviewCaseBill.economic_id > 0,
            ReviewCase.status == "CONFIRMED",
        ))


def _set_account(client, fact_id, review_version, account, key):
    return client.put(f"/paam/review/v1/account/set/{fact_id}", json={
        "account_code": account,
        "expected_version": review_version,
        "reason": "account evidence checked",
        "idempotency_key": key,
    })


def test_ledger_account_is_independent_from_transaction_fact(target_account_api):
    client, sessions = target_account_api
    fact_id = _facts(sessions, [("OUT", "fact-wallet")])[0]
    ledger_id = _ledger_id(sessions, fact_id)

    account = client.get(f"/paam/ledger/v1/flow/{ledger_id}/account")
    assert account.status_code == 200, account.text
    assert account.json()["body"]["account_code"] == "fact-wallet"

    updated = client.put(f"/paam/ledger/v1/flow/{ledger_id}/account", json={
        "account_code": "ledger-wallet",
        "expected_projection_version": account.json()["body"]["projection_version"],
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["body"]["account_code"] == "ledger-wallet"
    assert updated.json()["body"]["projection_version"] == 2

    with sessions() as db:
        assert db.get(BillFact, fact_id).account_code == "fact-wallet"
    detail = client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()["body"]
    assert detail["flow"]["account_code"] == "ledger-wallet"
    assert detail["facts"][0]["account_code"] == "fact-wallet"

    stale = client.put(f"/paam/ledger/v1/flow/{ledger_id}/account", json={
        "account_code": "stale-wallet",
        "expected_projection_version": 1,
    })
    assert stale.status_code == 409


def test_account_correction_keeps_fact_immutable_and_rebuilds_default_projection(
    target_account_api,
):
    client, sessions = target_account_api
    fact_id = _facts(sessions, [("OUT", "imported-wallet")])[0]
    ledger_id = _ledger_id(sessions, fact_id)
    corrected = _set_account(client, fact_id, 0, "checked-bank", "account-first")
    assert corrected.status_code == 200, corrected.text
    case = corrected.json()["body"]
    assert case["review_type"] == "ACCOUNT"
    assert case["result"] == {"account_name": "checked-bank"}
    assert case["lines"][0]["role"] == "ACCOUNT"

    detail = client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()["body"]
    assert detail["facts"][0]["account_code"] == "imported-wallet"
    assert detail["flow"]["account_code"] == "checked-bank"

    replay = _set_account(client, fact_id, 0, "checked-bank", "account-first")
    assert replay.status_code == 200
    assert _set_account(client, fact_id, 0, "other", "account-stale").status_code == 409

    updated = _set_account(client, fact_id, 1, "second-bank", "account-second")
    assert updated.status_code == 200, updated.text
    assert updated.json()["body"]["version"] == 2
    with sessions() as db:
        fact = db.get(BillFact, fact_id)
        assert fact.account_code == "imported-wallet"
        histories = db.scalars(select(ReviewHistory).where(
            ReviewHistory.case_id == case["id"]
        ).order_by(ReviewHistory.version)).all()
        assert len(histories) == 2
        assert '"account_name":"checked-bank"' in histories[1].before_json
        assert '"account_name":"second-bank"' in histories[1].after_json

    revoked = client.post(f"/paam/review/v1/account/revoke/{case['id']}", json={
        "expected_version": 2,
        "reason": "undo correction",
        "idempotency_key": "account-revoke",
    })
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["body"]["status"] == "REVOKED"
    reverted_detail = client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()["body"]
    assert reverted_detail["flow"]["account_code"] == "imported-wallet"

    restored = client.post(f"/paam/review/v1/account/restore/{case['id']}", json={
        "expected_version": 3,
        "reason": "restore correction",
        "idempotency_key": "account-restore",
    })
    assert restored.status_code == 200, restored.text
    assert restored.json()["body"]["status"] == "CONFIRMED"
    restored_detail = client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()["body"]
    assert restored_detail["flow"]["account_code"] == "second-bank"
    assert restored.json()["body"]["history"][-1]["reverses_history_id"] > 0


def test_account_correction_republishes_connected_financial_entry(
    target_account_api,
):
    client, sessions = target_account_api
    out_id, in_id = _facts(sessions, [
        ("OUT", "wallet-a"),
        ("IN", "wallet-b"),
    ])
    created = client.post("/paam/ledger/v1/review", json={
        "behavior_code": "TRANSFER",
        "economics": [
            {"client_key": "out", "economic_type": "ACCOUNT_TRANSFER"},
            {"client_key": "in", "economic_type": "ACCOUNT_TRANSFER"},
        ],
        "allocations": [
            {"fact_id": out_id, "economic_key": "out", "amount_value": 1000},
            {"fact_id": in_id, "economic_key": "in", "amount_value": 1000},
        ],
        "idempotency_key": "financial-create",
    }).json()["body"]
    assert client.post(f"/paam/ledger/v1/review/{created['id']}/confirm", json={
        "expected_version": 1,
        "idempotency_key": "financial-confirm",
    }).status_code == 200
    ledger_id = _ledger_id(sessions, out_id)
    before = client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()["body"]["flow"]
    assert before["account_code"] == "wallet-a"

    corrected = _set_account(client, out_id, 0, "checked-wallet", "merged-account")
    assert corrected.status_code == 200, corrected.text
    after = client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()["body"]
    assert after["flow"]["account_code"] == "checked-wallet"
    assert {item["review_type"] for item in after["reviews"]} == {"ACCOUNT", "TRANSFER"}


def test_account_code_rejects_projection_sentinel(target_account_api):
    client, sessions = target_account_api
    fact_id = _facts(sessions, [("OUT", "wallet")])[0]
    response = _set_account(client, fact_id, 0, "MULTIPLE", "bad-account")
    assert response.status_code == 422
    with sessions() as db:
        assert db.scalar(select(ReviewCase.id).where(
            ReviewCase.review_type == "ACCOUNT"
        )) is None
