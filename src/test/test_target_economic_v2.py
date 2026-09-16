from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.router.dependency import get_db
from backend.router.ledger import router as ledger_router
from backend.router.ledger_account import router as ledger_account_router
from backend.router.ledger_review import router as ledger_review_router
from backend.router.tag import router as tag_router
from backend.router.tag_assignment import router as tag_assignment_router
from backend.entity import (
    BillFact,
    LedgerEntry,
    LedgerEntryTag,
    ReviewCase,
    ReviewCaseBill,
)
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
    api.include_router(ledger_account_router)
    api.include_router(ledger_router)
    api.include_router(tag_router)
    api.include_router(tag_assignment_router)

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


def test_ledger_v1_exposes_only_confirmed_cash_entry_fields(economic_api):
    client, sessions = economic_api
    fact_id = _facts(sessions, [("OUT", 12345, "CNY")])[0]
    with sessions() as db:
        TargetEconomicService(db).ensure_defaults([fact_id], commit=True)

    page = client.get("/paam/ledger/v1/flow/list")
    assert page.status_code == 200, page.text
    item = page.json()["items"][0]
    assert set(item) == {
        "id",
        "entry_type",
        "entry_direction",
        "amount",
        "account_code",
        "counterparty_account_ref",
        "projection_version",
        "occurred_time",
        "tags",
    }
    assert (item["entry_type"], item["entry_direction"]) == (0, 2)
    detail = client.get(f"/paam/ledger/v1/flow/{item['id']}").json()
    assert "role" not in detail["allocations"][0]
    assert detail["entry"] == item
    assert client.get("/paam/economy/v1/flow/list").status_code == 404
    assert client.get("/paam/economy/v1/flow/detail/1").status_code == 404
    assert client.get("/paam/economy/v1/summary").status_code == 404


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
        "description": "垫付分摊",
        "entries": [
            {"client_key": "own", "entry_type": 0},
            {"client_key": "advance", "entry_type": 1},
            *[
                {"client_key": f"return-{index}", "entry_type": 1}
                for index in range(1, 5)
            ],
        ],
        "allocations": [
            {"transaction_fact_id": fact_ids[0], "entry_key": "own", "amount_value": 10000},
            {"transaction_fact_id": fact_ids[0], "entry_key": "advance", "amount_value": 40000},
            *[
                {
                    "transaction_fact_id": fact_id,
                    "entry_key": f"return-{index}",
                    "amount_value": 10000,
                }
                for index, fact_id in enumerate(fact_ids[1:], 1)
            ],
        ],
        "idempotency_key": "advance-create",
    })
    assert response.status_code == 200, response.text
    case = response.json()["body"]
    assert case["status"] == "PENDING"
    assert len(case["ledger_entries"]) == 6
    assert len(case["allocations"]) == 6
    with sessions() as db:
        assert db.scalar(select(func.count(LedgerEntry.id)).join(
            ReviewCaseBill, ReviewCaseBill.economic_id == LedgerEntry.id,
        ).where(ReviewCaseBill.case_id == case["id"])) == 0
        assert set(db.scalars(select(ReviewCaseBill.economic_id).where(
            ReviewCaseBill.case_id == case["id"],
        )).all()) == {0}

    confirmed = client.post(
        f"/paam/review/v2/case/confirm/{case['id']}",
        json={"expected_version": 1, "idempotency_key": "advance-confirm"},
    )
    assert confirmed.status_code == 200, confirmed.text
    summary = client.get("/paam/ledger/v1/flow/summary").json()
    assert summary["totals"] == [{
        "currency_code": "CNY",
        "amount_scale": 2,
        "transaction_in_value": 0,
        "transaction_out_value": 10000,
        "account_transfer_in_value": 40000,
        "account_transfer_out_value": 40000,
        "claim_cashflow_in_value": 0,
        "claim_cashflow_out_value": 0,
    }]
    with sessions() as db:
        assert db.scalar(select(func.count(LedgerEntry.id))) == 6
        coverage = dict(db.execute(select(
            ReviewCaseBill.bill_id,
            func.sum(ReviewCaseBill.amount_value),
        ).join(
            ReviewCase, ReviewCase.id == ReviewCaseBill.case_id,
        ).join(
            LedgerEntry, LedgerEntry.id == ReviewCaseBill.economic_id,
        ).where(
            ReviewCase.status == "CONFIRMED",
        ).group_by(ReviewCaseBill.bill_id)).all())
        assert coverage == dict(zip(fact_ids, [50000, 10000, 10000, 10000, 10000]))

    revoked = client.post(
        f"/paam/review/v2/case/revoke/{case['id']}",
        json={"expected_version": 2, "idempotency_key": "advance-revoke"},
    )
    assert revoked.status_code == 200, revoked.text
    summary = client.get("/paam/ledger/v1/flow/summary").json()["totals"][0]
    assert summary["transaction_in_value"] == 40000
    assert summary["transaction_out_value"] == 50000
    assert summary["account_transfer_in_value"] == 0
    assert summary["account_transfer_out_value"] == 0
    with sessions() as db:
        assert db.scalar(select(func.count(LedgerEntry.id))) == 5
        assert set(db.scalars(select(ReviewCaseBill.economic_id).where(
            ReviewCaseBill.case_id == case["id"],
        )).all()) == {0}


def test_loan_uses_one_claim_cashflow_entry_per_fact(economic_api):
    client, sessions = economic_api
    fact_ids = _facts(sessions, [
        ("OUT", 500000, "CNY"),
        ("OUT", 500000, "CNY"),
        ("IN", 300000, "CNY"),
        ("IN", 300000, "CNY"),
        ("IN", 400000, "CNY"),
    ])
    entries = [
        *[
            {
                "client_key": f"principal-{index}",
                "entry_type": 2,
            }
            for index in range(1, 3)
        ],
        *[
            {
                "client_key": f"repayment-{index}",
                "entry_type": 2,
            }
            for index in range(1, 4)
        ],
    ]
    allocations = [
        {"transaction_fact_id": fact_ids[0], "entry_key": "principal-1", "amount_value": 500000},
        {"transaction_fact_id": fact_ids[1], "entry_key": "principal-2", "amount_value": 500000},
        {"transaction_fact_id": fact_ids[2], "entry_key": "repayment-1", "amount_value": 300000},
        {"transaction_fact_id": fact_ids[3], "entry_key": "repayment-2", "amount_value": 300000},
        {"transaction_fact_id": fact_ids[4], "entry_key": "repayment-3", "amount_value": 400000},
    ]
    case = client.post("/paam/review/v2/case/create", json={
        "behavior_code": "LOAN",
        "entries": entries,
        "allocations": allocations,
        "idempotency_key": "loan-create",
    }).json()["body"]
    confirmed = client.post(
        f"/paam/review/v2/case/confirm/{case['id']}",
        json={"expected_version": 1, "idempotency_key": "loan-confirm"},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()["body"]
    assert sorted(item["amount_value"] for item in body["ledger_entries"]) == [
        300000, 300000, 400000, 500000, 500000,
    ]
    summary = client.get("/paam/ledger/v1/flow/summary").json()["totals"][0]
    assert summary["claim_cashflow_out_value"] == 1000000
    assert summary["claim_cashflow_in_value"] == 1000000


def test_one_economic_cannot_allocate_multiple_facts(economic_api):
    client, sessions = economic_api
    out_id, in_id = _facts(sessions, [("OUT", 12000, "CNY"), ("IN", 2000, "USD")])
    response = client.post("/paam/review/v2/case/create", json={
        "behavior_code": "FX_EXCHANGE",
        "entries": [{"client_key": "mixed", "entry_type": 1}],
        "allocations": [
            {"transaction_fact_id": out_id, "entry_key": "mixed", "amount_value": 12000},
            {"transaction_fact_id": in_id, "entry_key": "mixed", "amount_value": 2000},
        ],
        "idempotency_key": "fx-invalid",
    })
    assert response.status_code == 422


def test_partial_manual_reviews_keep_exact_default_coverage_and_are_idempotent(economic_api):
    client, sessions = economic_api
    fact_id = _facts(sessions, [("OUT", 10000, "CNY")])[0]
    created = client.post("/paam/review/v2/case/create", json={
        "behavior_code": "SPLIT_PURCHASE",
        "entries": [{"client_key": "part", "entry_type": 0}],
        "allocations": [{
            "transaction_fact_id": fact_id,
            "entry_key": "part",
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
    assert cases["items"][0]["ledger_entry_count"] == 1
    assert cases["items"][0]["allocation_count"] == 1

    with sessions() as db:
        coverage = db.scalar(select(func.sum(ReviewCaseBill.amount_value)).join(
            ReviewCase, ReviewCase.id == ReviewCaseBill.case_id,
        ).join(
            LedgerEntry, LedgerEntry.id == ReviewCaseBill.economic_id,
        ).where(
            ReviewCaseBill.bill_id == fact_id,
            ReviewCase.status == "CONFIRMED",
        ))
        assert coverage == 10000


def test_fx_review_uses_two_single_currency_account_transfers(economic_api):
    client, sessions = economic_api
    cny_id, usd_id = _facts(sessions, [("OUT", 12000, "CNY"), ("IN", 2000, "USD")])
    case = client.post("/paam/review/v2/case/create", json={
        "behavior_code": "FX_EXCHANGE",
        "entries": [
            {"client_key": "cny", "entry_type": 1},
            {"client_key": "usd", "entry_type": 1},
        ],
        "allocations": [
            {"transaction_fact_id": cny_id, "entry_key": "cny", "amount_value": 12000},
            {"transaction_fact_id": usd_id, "entry_key": "usd", "amount_value": 2000},
        ],
        "idempotency_key": "fx-create",
    }).json()["body"]
    response = client.post(f"/paam/review/v2/case/confirm/{case['id']}", json={
        "expected_version": 1,
        "idempotency_key": "fx-confirm",
    })
    assert response.status_code == 200, response.text
    flows = response.json()["body"]["ledger_entries"]
    assert {(item["entry_direction"], item["amount_value"], item["currency_code"]) for item in flows} == {
        (2, 12000, "CNY"),
        (1, 2000, "USD"),
    }
    totals = client.get("/paam/ledger/v1/flow/summary").json()["totals"]
    assert {(item["currency_code"], item["account_transfer_in_value"], item["account_transfer_out_value"]) for item in totals} == {
        ("CNY", 0, 12000),
        ("USD", 2000, 0),
    }


def test_review_v2_rejects_removed_fields(economic_api):
    client, sessions = economic_api
    fact_id = _facts(sessions, [("IN", 4000, "CNY")])[0]
    rejected = client.post("/paam/review/v2/case/create", json={
        "behavior_code": "REFUND",
        "title": "legacy title",
        "economics": [{
            "client_key": "refund",
            "economic_type": "TRANSACTION",
            "reversal_of_id": 1,
        }],
        "entries": [{"client_key": "refund", "entry_type": 0}],
        "allocations": [{
            "transaction_fact_id": fact_id,
            "entry_key": "refund",
            "amount_value": 4000,
        }],
        "idempotency_key": "legacy-contract",
    })
    assert rejected.status_code == 422


def test_tag_sync_uses_allocations_for_every_split_ledger_entry(economic_api):
    client, sessions = economic_api
    fact_id = _facts(sessions, [("OUT", 10000, "CNY")])[0]
    with sessions() as db:
        TargetEconomicService(db).ensure_defaults([fact_id], commit=True)
    view = client.post("/paam/tag/v1/view", json={
        "name": "Category",
        "system_name": "category",
    }).json()["body"]
    case = client.post("/paam/review/v2/case/create", json={
        "behavior_code": "SPLIT_PURCHASE",
        "entries": [
            {"client_key": "goods", "entry_type": 0},
            {"client_key": "service", "entry_type": 0},
        ],
        "allocations": [
            {"transaction_fact_id": fact_id, "entry_key": "goods", "amount_value": 6000},
            {"transaction_fact_id": fact_id, "entry_key": "service", "amount_value": 4000},
        ],
        "idempotency_key": "split-tag-create",
    }).json()["body"]
    confirmed = client.post(f"/paam/review/v2/case/confirm/{case['id']}", json={
        "expected_version": 1,
        "idempotency_key": "split-tag-confirm",
    })
    assert confirmed.status_code == 200, confirmed.text
    ledger_ids = [row["id"] for row in confirmed.json()["body"]["ledger_entries"]]
    with sessions() as db:
        assert db.query(LedgerEntryTag).count() == 2

    tagged_view = client.post(f"/paam/tag/v1/view/{view['id']}/tag", json={
        "name": "Food",
        "system_name": "food",
    })
    assert tagged_view.status_code == 200, tagged_view.text
    detail = client.get(f"/paam/ledger/v1/flow/{ledger_ids[0]}").json()
    assigned = client.put(f"/paam/tag/v1/assignment/{ledger_ids[0]}", json={
        "tag_state": {"category": "food"},
        "expected_projection_version": detail["entry"]["projection_version"],
    })
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["body"]["projection_version"] == 2
    details = [
        client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()
        for ledger_id in ledger_ids
    ]
    assert details[0]["entry"]["tags"][0]["tag_system_name"] == "food"
    assert details[1]["entry"]["tags"][0]["tag_system_name"] == "unclassified"

    archived = client.put(f"/paam/tag/v1/view/{view['id']}", json={
        "status": "ARCHIVED",
    })
    assert archived.status_code == 200, archived.text
    with sessions() as db:
        assert db.query(LedgerEntryTag).count() == 0

    restored = client.put(f"/paam/tag/v1/view/{view['id']}", json={
        "status": "ACTIVE",
    })
    assert restored.status_code == 200, restored.text
    with sessions() as db:
        assert db.query(LedgerEntryTag).count() == 2


def test_account_review_updates_every_split_ledger_and_survives_rebuild(economic_api):
    client, sessions = economic_api
    fact_id = _facts(sessions, [("OUT", 10000, "CNY")])[0]
    with sessions() as db:
        TargetEconomicService(db).ensure_defaults([fact_id], commit=True)
    case = client.post("/paam/review/v2/case/create", json={
        "behavior_code": "SPLIT_PURCHASE",
        "entries": [
            {"client_key": "goods", "entry_type": 0},
            {"client_key": "service", "entry_type": 0},
        ],
        "allocations": [
            {"transaction_fact_id": fact_id, "entry_key": "goods", "amount_value": 6000},
            {"transaction_fact_id": fact_id, "entry_key": "service", "amount_value": 4000},
        ],
        "idempotency_key": "split-account-create",
    }).json()["body"]
    confirmed = client.post(f"/paam/review/v2/case/confirm/{case['id']}", json={
        "expected_version": 1,
        "idempotency_key": "split-account-confirm",
    })
    assert confirmed.status_code == 200, confirmed.text
    ledger_ids = [row["id"] for row in confirmed.json()["body"]["ledger_entries"]]
    corrected = client.put(f"/paam/review/v1/account/set/{fact_id}", json={
        "account_code": "checked-bank",
        "expected_version": 0,
        "idempotency_key": "split-account-set",
    })
    assert corrected.status_code == 200, corrected.text
    details = [
        client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()
        for ledger_id in ledger_ids
    ]
    assert all(item["entry"]["account_code"] == "checked-bank" for item in details)
    assert all(item["facts"][0]["account_review_version"] == 1 for item in details)
    assert all(
        {review["review_type"] for review in item["reviews"]}
        == {"SPLIT_PURCHASE", "ACCOUNT"}
        for item in details
    )
    with sessions() as db:
        assert db.get(BillFact, fact_id).account_code == "account-1"

    revoked = client.post(f"/paam/review/v2/case/revoke/{case['id']}", json={
        "expected_version": 2,
        "idempotency_key": "split-account-review-revoke",
    })
    assert revoked.status_code == 200, revoked.text
    rebuilt = client.get("/paam/ledger/v1/flow/list").json()["items"]
    assert len(rebuilt) == 1
    assert rebuilt[0]["account_code"] == "checked-bank"

    restored = client.post(f"/paam/review/v2/case/restore/{case['id']}", json={
        "expected_version": 3,
        "idempotency_key": "split-account-review-restore",
    })
    assert restored.status_code == 200, restored.text
    assert {
        item["account_code"]
        for item in client.get("/paam/ledger/v1/flow/list").json()["items"]
    } == {"checked-bank"}

def test_ledger_entry_entity_has_only_cash_projection_columns():
    assert set(LedgerEntry.__table__.columns.keys()) == {
        "id",
        "entry_type",
        "entry_direction",
        "amount_value",
        "amount_scale",
        "currency_code",
        "account_code",
        "counterparty_account_ref",
        "projection_version",
        "occurred_time",
        "created_time",
        "updated_time",
    }
