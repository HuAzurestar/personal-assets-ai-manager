from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.router.ledger import router as ledger_router
from backend.router.ledger_review import router as ledger_review_router
from backend.router.dependency import get_db
from backend.entity import BillFact, LedgerEntry, LedgerEntrySource, ReviewCase, ReviewCaseBill
from backend.service.target_economic_service import TargetEconomicService
from backend.core.target_database import init_target_db


@pytest.fixture
def economic_api(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'economic-v2.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False)
    init_target_db(bind=engine)
    api = FastAPI()
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
    now = datetime(2026, 9, 13, 10)
    with sessions() as db:
        rows = [BillFact(
            fact_key=f"economic-v2-{uuid4().hex}",
            occurred_time=now + timedelta(minutes=index),
            cash_direction=direction,
            amount_value=amount,
            amount_scale=2,
            currency_code=currency,
            account_code=f"account-{index}",
            counterparty="counterparty",
            summary="fact",
            created_time=now,
            updated_time=now,
        ) for index, (direction, amount, currency) in enumerate(specifications, 1)]
        db.add_all(rows)
        db.commit()
        return [row.id for row in rows]


def test_advance_review_is_ternary_exact_and_revoke_restores_defaults(economic_api):
    client, sessions = economic_api
    fact_ids = _facts(sessions, [
        ("OUT", 50000, "CNY"),
        ("IN", 10000, "CNY"),
        ("IN", 10000, "CNY"),
        ("IN", 10000, "CNY"),
        ("IN", 10000, "CNY"),
    ])
    response = client.post("/paam/review/v2/case/create", json={
        "behavior_code": "ADVANCE",
        "title": "垫付分摊",
        "economics": [
            {"client_key": "own", "economic_type": "TRANSACTION"},
            {"client_key": "advance", "economic_type": "ACCOUNT_TRANSFER"},
            {"client_key": "returns", "economic_type": "ACCOUNT_TRANSFER"},
        ],
        "allocations": [
            {"fact_id": fact_ids[0], "economic_key": "own", "amount_value": 10000},
            {"fact_id": fact_ids[0], "economic_key": "advance", "amount_value": 40000},
            *[
                {"fact_id": fact_id, "economic_key": "returns", "amount_value": 10000}
                for fact_id in fact_ids[1:]
            ],
        ],
        "idempotency_key": "advance-create",
    })
    assert response.status_code == 200, response.text
    case = response.json()["body"]
    assert case["status"] == "PENDING"
    assert len(case["economics"]) == 3
    assert len(case["allocations"]) == 6

    confirmed = client.post(
        f"/paam/review/v2/case/confirm/{case['id']}",
        json={"expected_version": 1, "idempotency_key": "advance-confirm"},
    )
    assert confirmed.status_code == 200, confirmed.text
    summary = client.get("/paam/economy/v1/summary").json()
    assert summary["totals"] == [{
        "currency_code": "CNY",
        "amount_scale": 2,
        "income_value": 0,
        "expense_value": 10000,
        "reversal_in_value": 0,
        "reversal_out_value": 0,
        "account_transfer_in_value": 40000,
        "account_transfer_out_value": 40000,
        "account_transfer_net_value": 0,
        "claim_in_value": 0,
        "claim_out_value": 0,
        "receivable_balance_value": 0,
        "payable_balance_value": 0,
    }]
    with sessions() as db:
        coverage = dict(db.execute(select(
            ReviewCaseBill.bill_id,
            func.sum(ReviewCaseBill.amount_value),
        ).join(
            ReviewCase, ReviewCase.id == ReviewCaseBill.case_id,
        ).join(
            LedgerEntry, LedgerEntry.id == ReviewCaseBill.economic_id,
        ).where(
            ReviewCase.status == "CONFIRMED",
            LedgerEntry.status == "ACTIVE",
        ).group_by(ReviewCaseBill.bill_id)).all())
        assert coverage == dict(zip(fact_ids, [50000, 10000, 10000, 10000, 10000]))

    revoked = client.post(
        f"/paam/review/v2/case/revoke/{case['id']}",
        json={"expected_version": 2, "idempotency_key": "advance-revoke"},
    )
    assert revoked.status_code == 200, revoked.text
    summary = client.get("/paam/economy/v1/summary").json()["totals"][0]
    assert summary["income_value"] == 40000
    assert summary["expense_value"] == 50000
    assert summary["account_transfer_in_value"] == 0
    assert summary["account_transfer_out_value"] == 0


def test_loan_many_facts_compose_claim_and_balance_to_zero(economic_api):
    client, sessions = economic_api
    fact_ids = _facts(sessions, [
        ("OUT", 500000, "CNY"),
        ("OUT", 500000, "CNY"),
        ("IN", 300000, "CNY"),
        ("IN", 300000, "CNY"),
        ("IN", 400000, "CNY"),
    ])
    economics = [
        {
            "client_key": "principal",
            "economic_type": "CLAIM",
            "claim_key": "loan-alice",
            "claim_side": "RECEIVABLE",
        },
        *[
            {
                "client_key": f"repayment-{index}",
                "economic_type": "CLAIM",
                "claim_key": "loan-alice",
                "claim_side": "RECEIVABLE",
            }
            for index in range(1, 4)
        ],
    ]
    allocations = [
        {"fact_id": fact_ids[0], "economic_key": "principal", "amount_value": 500000},
        {"fact_id": fact_ids[1], "economic_key": "principal", "amount_value": 500000},
        {"fact_id": fact_ids[2], "economic_key": "repayment-1", "amount_value": 300000},
        {"fact_id": fact_ids[3], "economic_key": "repayment-2", "amount_value": 300000},
        {"fact_id": fact_ids[4], "economic_key": "repayment-3", "amount_value": 400000},
    ]
    case = client.post("/paam/review/v2/case/create", json={
        "behavior_code": "LOAN",
        "economics": economics,
        "allocations": allocations,
        "idempotency_key": "loan-create",
    }).json()["body"]
    confirmed = client.post(
        f"/paam/review/v2/case/confirm/{case['id']}",
        json={"expected_version": 1, "idempotency_key": "loan-confirm"},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()["body"]
    assert sorted(item["amount_value"] for item in body["economics"]) == [
        300000, 300000, 400000, 1000000,
    ]
    summary = client.get("/paam/economy/v1/summary").json()["totals"][0]
    assert summary["claim_out_value"] == 1000000
    assert summary["claim_in_value"] == 1000000
    assert summary["receivable_balance_value"] == 0


def test_one_economic_cannot_mix_direction_or_currency(economic_api):
    client, sessions = economic_api
    out_id, in_id = _facts(sessions, [("OUT", 12000, "CNY"), ("IN", 2000, "USD")])
    response = client.post("/paam/review/v2/case/create", json={
        "behavior_code": "FX_EXCHANGE",
        "economics": [{"client_key": "mixed", "economic_type": "ACCOUNT_TRANSFER"}],
        "allocations": [
            {"fact_id": out_id, "economic_key": "mixed", "amount_value": 12000},
            {"fact_id": in_id, "economic_key": "mixed", "amount_value": 2000},
        ],
        "idempotency_key": "fx-invalid",
    })
    assert response.status_code == 422


def test_partial_manual_reviews_keep_exact_default_coverage_and_are_idempotent(economic_api):
    client, sessions = economic_api
    fact_id = _facts(sessions, [("OUT", 10000, "CNY")])[0]
    created = client.post("/paam/review/v2/case/create", json={
        "behavior_code": "SPLIT_PURCHASE",
        "economics": [{"client_key": "part", "economic_type": "TRANSACTION"}],
        "allocations": [{
            "fact_id": fact_id,
            "economic_key": "part",
            "amount_value": 4000,
        }],
        "idempotency_key": "partial-create",
    }).json()["body"]
    payload = {"expected_version": 1, "idempotency_key": "partial-confirm"}
    first = client.post(f"/paam/review/v2/case/confirm/{created['id']}", json=payload)
    replay = client.post(f"/paam/review/v2/case/confirm/{created['id']}", json=payload)
    assert first.status_code == replay.status_code == 200
    assert first.json()["body"] == replay.json()["body"]

    candidates = client.get("/paam/review/v2/fact/candidates").json()["body"]
    assert [(item["id"], item["available_value"]) for item in candidates] == [(fact_id, 6000)]
    cases = client.get("/paam/review/v2/case/page").json()["body"]
    assert cases["total"] == 1
    assert cases["items"][0]["economic_count"] == 1
    assert cases["items"][0]["allocation_count"] == 1

    with sessions() as db:
        coverage = db.scalar(select(func.sum(ReviewCaseBill.amount_value)).join(
            ReviewCase, ReviewCase.id == ReviewCaseBill.case_id,
        ).join(
            LedgerEntry, LedgerEntry.id == ReviewCaseBill.economic_id,
        ).where(
            ReviewCaseBill.bill_id == fact_id,
            ReviewCase.status == "CONFIRMED",
            LedgerEntry.status == "ACTIVE",
        ))
        assert coverage == 10000


def test_fx_review_uses_two_single_currency_account_transfers(economic_api):
    client, sessions = economic_api
    cny_id, usd_id = _facts(sessions, [("OUT", 12000, "CNY"), ("IN", 2000, "USD")])
    case = client.post("/paam/review/v2/case/create", json={
        "behavior_code": "FX_EXCHANGE",
        "economics": [
            {"client_key": "cny", "economic_type": "ACCOUNT_TRANSFER"},
            {"client_key": "usd", "economic_type": "ACCOUNT_TRANSFER"},
        ],
        "allocations": [
            {"fact_id": cny_id, "economic_key": "cny", "amount_value": 12000},
            {"fact_id": usd_id, "economic_key": "usd", "amount_value": 2000},
        ],
        "idempotency_key": "fx-create",
    }).json()["body"]
    response = client.post(f"/paam/review/v2/case/confirm/{case['id']}", json={
        "expected_version": 1,
        "idempotency_key": "fx-confirm",
    })
    assert response.status_code == 200, response.text
    flows = response.json()["body"]["economics"]
    assert {(item["cash_direction"], item["amount_value"], item["currency_code"]) for item in flows} == {
        ("OUT", 12000, "CNY"),
        ("IN", 2000, "USD"),
    }
    totals = client.get("/paam/economy/v1/summary").json()["totals"]
    assert {(item["currency_code"], item["account_transfer_in_value"], item["account_transfer_out_value"]) for item in totals} == {
        ("CNY", 0, 12000),
        ("USD", 2000, 0),
    }


def test_transaction_reversal_is_linked_and_cannot_exceed_original(economic_api):
    client, sessions = economic_api
    original_id, refund_1, refund_2 = _facts(sessions, [
        ("OUT", 10000, "CNY"),
        ("IN", 4000, "CNY"),
        ("IN", 7000, "CNY"),
    ])
    seed = client.post("/paam/review/v2/case/create", json={
        "behavior_code": "SEED_DEFAULT",
        "economics": [{"client_key": "unused", "economic_type": "TRANSACTION"}],
        "allocations": [{"fact_id": original_id, "economic_key": "unused", "amount_value": 1}],
        "idempotency_key": "seed-default",
    })
    assert seed.status_code == 200
    original_economic_id = client.get(
        "/paam/economy/v1/flow/list?economic_type=TRANSACTION"
    ).json()["items"][0]["id"]

    def reversal(fact_id, amount, suffix):
        return client.post("/paam/review/v2/case/create", json={
            "behavior_code": "REFUND",
            "economics": [{
                "client_key": "refund",
                "economic_type": "TRANSACTION",
                "reversal_of_id": original_economic_id,
            }],
            "allocations": [{
                "fact_id": fact_id,
                "economic_key": "refund",
                "amount_value": amount,
            }],
            "idempotency_key": f"refund-create-{suffix}",
        }).json()["body"]

    first = reversal(refund_1, 4000, "one")
    confirmed = client.post(f"/paam/review/v2/case/confirm/{first['id']}", json={
        "expected_version": 1,
        "idempotency_key": "refund-confirm-one",
    })
    assert confirmed.status_code == 200
    assert confirmed.json()["body"]["economics"][0]["reversal_of_id"] == original_economic_id

    rejected = client.post("/paam/review/v2/case/create", json={
        "behavior_code": "REFUND",
        "economics": [{
            "client_key": "refund",
            "economic_type": "TRANSACTION",
            "reversal_of_id": original_economic_id,
        }],
        "allocations": [{
            "fact_id": refund_2,
            "economic_key": "refund",
            "amount_value": 7000,
        }],
        "idempotency_key": "refund-create-two",
    })
    assert rejected.status_code == 422
    assert rejected.json()["status"] == 422
    assert "exceed" in rejected.json()["message"]
    assert rejected.json()["body"]["code"] == "ECONOMIC_ERROR"


def test_backfill_does_not_reuse_a_legacy_aggregate_for_multiple_facts(economic_api):
    client, sessions = economic_api
    first_id, second_id = _facts(sessions, [("OUT", 3000, "CNY"), ("OUT", 7000, "CNY")])
    now = datetime(2026, 9, 13, 12)
    with sessions() as db:
        legacy = LedgerEntry(
            ledger_type="AA",
            allocation_status="COMPLETE",
            title="legacy aggregate",
            start_time=now,
            end_time=now,
            out_amount_value=10000,
            created_time=now,
            updated_time=now,
        )
        db.add(legacy)
        db.flush()
        db.add_all([
            LedgerEntrySource(ledger_id=legacy.id, source_kind="BILL_FACT", source_id=first_id),
            LedgerEntrySource(ledger_id=legacy.id, source_kind="BILL_FACT", source_id=second_id),
        ])
        db.commit()
        legacy_id = legacy.id

    with sessions() as db:
        TargetEconomicService(db).backfill_defaults()
    flows = client.get("/paam/economy/v1/flow/list").json()
    assert flows["total"] == 2
    assert legacy_id not in {item["id"] for item in flows["items"]}
    assert sorted(item["amount"]["amount_value"] for item in flows["items"]) == [3000, 7000]
