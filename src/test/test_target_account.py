from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.router.target_ledger import v1_router as target_ledger_router
from backend.router.ledger_account import router as ledger_account_router
from backend.router.ledger_review_legacy import router as ledger_review_legacy_router
from backend.router.dependency import get_db
from backend.core.target_database import init_target_db
from backend.entity import BillFact, LedgerEntrySource, ReviewCase, ReviewHistory
from backend.service.target_projection_service import TargetProjectionService


@pytest.fixture
def target_account_api(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'target-account.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False)
    init_target_db(bind=engine)
    api = FastAPI()
    api.include_router(ledger_review_legacy_router)
    api.include_router(ledger_account_router)
    api.include_router(target_ledger_router)

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
        TargetProjectionService(db).rebuild_defaults(fact_ids)
        db.commit()
        return fact_ids


def _ledger_id(sessions, fact_id):
    with sessions() as db:
        return db.scalar(select(LedgerEntrySource.ledger_id).where(
            LedgerEntrySource.source_kind == "BILL_FACT",
            LedgerEntrySource.source_id == fact_id,
        ))


def _set_account(client, fact_id, projection_version, account, key):
    return client.put(f"/paam/review/v1/account/set/{fact_id}", json={
        "account_code": account,
        "expected_projection_version": projection_version,
        "reason": "account evidence checked",
        "idempotency_key": key,
    })


def test_account_correction_keeps_fact_immutable_and_rebuilds_default_projection(
    target_account_api,
):
    client, sessions = target_account_api
    fact_id = _facts(sessions, [("OUT", "imported-wallet")])[0]
    ledger_id = _ledger_id(sessions, fact_id)
    corrected = _set_account(client, fact_id, 1, "checked-bank", "account-first")
    assert corrected.status_code == 200, corrected.text
    case = corrected.json()["body"]
    assert case["review_type"] == "ACCOUNT"
    assert case["result"] == {"account_name": "checked-bank"}
    assert case["lines"][0]["role"] == "ACCOUNT"

    detail = client.get(f"/paam/ledger/v1/entry/detail/{ledger_id}").json()
    assert detail["facts"][0]["account_code"] == "imported-wallet"
    assert detail["entry"]["out_account_code"] == "checked-bank"
    assert detail["reviews"][0]["is_projection_source"] is True

    replay = _set_account(client, fact_id, 1, "checked-bank", "account-first")
    assert replay.status_code == 200
    assert _set_account(client, fact_id, 1, "other", "account-stale").status_code == 409

    updated = _set_account(client, fact_id, 2, "second-bank", "account-second")
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
    reverted_detail = client.get(f"/paam/ledger/v1/entry/detail/{ledger_id}").json()
    assert reverted_detail["entry"]["out_account_code"] == "imported-wallet"

    restored = client.post(f"/paam/review/v1/account/restore/{case['id']}", json={
        "expected_version": 3,
        "reason": "restore correction",
        "idempotency_key": "account-restore",
    })
    assert restored.status_code == 200, restored.text
    assert restored.json()["body"]["status"] == "CONFIRMED"
    restored_detail = client.get(f"/paam/ledger/v1/entry/detail/{ledger_id}").json()
    assert restored_detail["entry"]["out_account_code"] == "second-bank"
    assert restored.json()["body"]["history"][-1]["reverses_history_id"] > 0


def test_account_correction_republishes_connected_financial_entry(
    target_account_api,
):
    client, sessions = target_account_api
    out_id, in_id = _facts(sessions, [
        ("OUT", "wallet-a"),
        ("IN", "wallet-b"),
    ])
    created = client.post("/paam/review/v1/case/create", json={
        "review_type": "TRANSFER",
        "title": "Transfer",
        "result": {},
        "lines": [
            {"bill_id": out_id, "role": "TRANSFER_OUT"},
            {"bill_id": in_id, "role": "TRANSFER_IN"},
        ],
        "idempotency_key": "financial-create",
    }).json()["body"]
    assert client.post(f"/paam/review/v1/case/confirm/{created['id']}", json={
        "expected_version": 1,
        "idempotency_key": "financial-confirm",
    }).status_code == 200
    ledger_id = _ledger_id(sessions, out_id)
    before = client.get(f"/paam/ledger/v1/entry/detail/{ledger_id}").json()["entry"]
    assert before["projection_version"] == 2
    assert before["out_account_code"] == "wallet-a"

    corrected = _set_account(client, out_id, 2, "checked-wallet", "merged-account")
    assert corrected.status_code == 200, corrected.text
    after = client.get(f"/paam/ledger/v1/entry/detail/{ledger_id}").json()
    assert after["entry"]["projection_version"] == 3
    assert after["entry"]["out_account_code"] == "checked-wallet"
    assert after["entry"]["in_account_code"] == "wallet-b"
    assert {item["review_type"] for item in after["reviews"]} == {"ACCOUNT", "TRANSFER"}


def test_account_code_rejects_projection_sentinel(target_account_api):
    client, sessions = target_account_api
    fact_id = _facts(sessions, [("OUT", "wallet")])[0]
    response = _set_account(client, fact_id, 1, "MULTIPLE", "bad-account")
    assert response.status_code == 422
    with sessions() as db:
        assert db.scalar(select(ReviewCase.id)) is None
