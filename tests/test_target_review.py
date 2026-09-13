from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.api.controllers.target_ledger import v1_router as target_ledger_router
from app.api.controllers.target_review import router as target_review_router
from app.api.target_deps import get_target_db
from app.target_database import init_target_db
from app.models.target import BillFact, LedgerEntry, ReviewCase, ReviewHistory
from app.services.target_projection_service import TargetProjectionService


@pytest.fixture
def target_review_api(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'target-review.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False)
    init_target_db(bind=engine)
    api = FastAPI()
    api.include_router(target_review_router)
    api.include_router(target_ledger_router)

    def override_db():
        with sessions() as db:
            yield db

    api.dependency_overrides[get_target_db] = override_db
    with TestClient(api) as client:
        yield client, sessions, engine
    engine.dispose()


def _facts(sessions, specifications):
    now = datetime(2026, 9, 12, 10)
    with sessions() as db:
        rows = []
        for index, (direction, value, currency) in enumerate(specifications, 1):
            fact = BillFact(
                fact_key=f"review-fact-{uuid4().hex}",
                occurred_time=now + timedelta(minutes=index),
                cash_direction=direction,
                amount_value=value,
                amount_scale=2,
                currency_code=currency,
                account_code=f"account-{index}",
                counterparty=f"counterparty-{index}",
                summary=f"fact-{index}",
                created_time=now,
                updated_time=now,
            )
            db.add(fact)
            rows.append(fact)
        db.flush()
        ids = [row.id for row in rows]
        TargetProjectionService(db).rebuild_defaults(ids)
        db.commit()
        return ids


def _create(client, review_type, lines, key="create-case", **values):
    return client.post("/paam/review/v1/case/create", json={
        "review_type": review_type,
        "title": values.get("title", review_type),
        "result": values.get("result", {}),
        "lines": lines,
        "reason": "test",
        "idempotency_key": key,
    })


@pytest.mark.parametrize(
    ("review_type", "specifications", "roles", "expected_type"),
    [
        ("AA", [("OUT", 1200, "CNY"), ("IN", 1000, "CNY")], ["AA_PAID", "AA_RECEIVED"], "AA"),
        ("LOAN_BORROW", [("IN", 1000, "CNY"), ("OUT", 1000, "CNY")], ["LOAN_RECEIVED", "LOAN_REPAID"], "LOAN_BORROW"),
        ("LOAN_LEND", [("OUT", 1000, "CNY"), ("IN", 1000, "CNY")], ["LOAN_LENT", "LOAN_RECOVERED"], "LOAN_LEND"),
        ("REFUND", [("IN", 1000, "CNY"), ("OUT", 1000, "CNY")], ["REFUND_RECEIVED", "REFUND_EXPENSE"], "REFUND"),
        ("TRANSFER", [("OUT", 1000, "CNY"), ("IN", 999, "CNY")], ["TRANSFER_OUT", "TRANSFER_IN"], "TRANSFER"),
        ("FX_EXCHANGE", [("OUT", 700, "CNY"), ("IN", 100, "USD")], ["FX_OUT", "FX_IN"], "FX_EXCHANGE"),
        ("DUPLICATE", [("OUT", 1000, "CNY"), ("OUT", 1000, "CNY")], ["DUPLICATE_RETAINED", "DUPLICATE_EXCLUDED"], "EXPENSE"),
    ],
)
def test_financial_review_types_publish_one_reversible_projection(
    target_review_api,
    review_type,
    specifications,
    roles,
    expected_type,
):
    client, sessions, _engine = target_review_api
    ids = _facts(sessions, specifications)
    response = _create(client, review_type, [
        {"bill_id": bill_id, "role": role}
        for bill_id, role in zip(ids, roles)
    ])
    assert response.status_code == 200, response.text
    case = response.json()["body"]
    confirmed = client.post(
        f"/paam/review/v1/case/confirm/{case['id']}",
        json={
            "expected_version": 1,
            "reason": "confirm",
            "idempotency_key": "confirm-case",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    page = client.get("/paam/ledger/v1/entry/list").json()
    assert page["total"] == 1
    assert page["items"][0]["ledger_type"] == expected_type

    revoked = client.post(
        f"/paam/review/v1/case/revoke/{case['id']}",
        json={
            "expected_version": 2,
            "reason": "revoke",
            "idempotency_key": "revoke-case",
        },
    )
    assert revoked.status_code == 200, revoked.text
    assert client.get("/paam/ledger/v1/entry/list").json()["total"] == 2


def test_review_policy_rejects_invalid_direction_overallocation_and_overlap(
    target_review_api,
):
    client, sessions, _engine = target_review_api
    out_id, in_id = _facts(sessions, [("OUT", 1000, "CNY"), ("IN", 1000, "CNY")])
    wrong_direction = _create(client, "TRANSFER", [
        {"bill_id": out_id, "role": "TRANSFER_IN"},
        {"bill_id": in_id, "role": "TRANSFER_OUT"},
    ], key="wrong-direction")
    assert wrong_direction.status_code == 422
    overallocated = _create(client, "AA", [
        {"bill_id": out_id, "role": "AA_PAID", "amount_value": 1001},
    ], key="overallocated")
    assert overallocated.status_code == 422

    first = _create(client, "TRANSFER", [
        {"bill_id": out_id, "role": "TRANSFER_OUT"},
        {"bill_id": in_id, "role": "TRANSFER_IN"},
    ], key="first-create").json()["body"]
    assert client.post(
        f"/paam/review/v1/case/confirm/{first['id']}",
        json={
            "expected_version": 1,
            "idempotency_key": "first-confirm",
        },
    ).status_code == 200
    second = _create(client, "AA", [
        {"bill_id": out_id, "role": "AA_PAID"},
    ], key="second-create").json()["body"]
    conflict = client.post(
        f"/paam/review/v1/case/confirm/{second['id']}",
        json={
            "expected_version": 1,
            "idempotency_key": "second-confirm",
        },
    )
    assert conflict.status_code == 409


def test_pending_review_update_replaces_current_lines_and_preserves_history(
    target_review_api,
):
    client, sessions, _engine = target_review_api
    fact_id = _facts(sessions, [("OUT", 1000, "CNY")])[0]
    case = _create(client, "AA", [{
        "bill_id": fact_id,
        "role": "AA_PAID",
        "amount_value": 400,
    }], key="update-create").json()["body"]
    assert case["allocation_status"] == "PARTIAL"

    updated = client.put(
        f"/paam/review/v1/case/update/{case['id']}",
        json={
            "expected_version": 1,
            "title": "完整 AA 分配",
            "result": {"note": "confirmed by user"},
            "lines": [{"bill_id": fact_id, "role": "AA_PAID"}],
            "reason": "补全金额",
            "idempotency_key": "update-lines",
        },
    )
    assert updated.status_code == 200, updated.text
    case = updated.json()["body"]
    assert case["version"] == 2
    assert case["allocation_status"] == "COMPLETE"
    assert case["lines"][0]["amount_value"] == 1000
    assert [item["operation"] for item in case["history"]] == ["CREATE", "UPDATE"]
    assert case["history"][1]["before"]["lines"][0]["amount_value"] == 400
    assert case["history"][1]["after"]["lines"][0]["amount_value"] == 1000

    confirmed = client.post(
        f"/paam/review/v1/case/confirm/{case['id']}",
        json={"expected_version": 2, "idempotency_key": "update-confirm"},
    )
    assert confirmed.status_code == 200
    blocked = client.put(
        f"/paam/review/v1/case/update/{case['id']}",
        json={
            "expected_version": 3,
            "lines": [{"bill_id": fact_id, "role": "AA_PAID"}],
            "idempotency_key": "confirmed-update",
        },
    )
    assert blocked.status_code == 409


def test_review_history_is_complete_hashed_and_list_queries_are_bounded(
    target_review_api,
):
    client, sessions, engine = target_review_api
    fact_ids = _facts(sessions, [("OUT", 1000, "CNY") for _ in range(20)])
    statements = []

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    for index, fact_id in enumerate(fact_ids):
        created = _create(client, "AA", [
            {"bill_id": fact_id, "role": "AA_PAID", "amount_value": 500},
        ], key=f"create-aa-{index}")
        assert created.status_code == 200, created.text

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        listed = client.get("/paam/review/v1/case/list?limit=100")
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    assert listed.status_code == 200
    assert len(listed.json()["body"]) == 20
    assert len(statements) == 3
    assert all("SELECT *" not in statement.upper() for statement in statements)

    case = listed.json()["body"][0]
    history = case["history"][0]
    import hashlib
    import json

    canonical = json.dumps(
        history["after"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert history["snapshot_hash"] == hashlib.sha256(canonical.encode()).hexdigest()
    with sessions() as db:
        assert db.scalar(select(ReviewHistory).where(
            ReviewHistory.id == history["id"]
        )).before_json == "{}"


def test_confirm_select_count_is_independent_of_case_size(target_review_api):
    client, sessions, engine = target_review_api

    def confirmed_selects(count, prefix):
        fact_ids = _facts(sessions, [("OUT", 1000, "CNY") for _ in range(count)])
        case = _create(client, "AA", [
            {"bill_id": fact_id, "role": "AA_PAID"} for fact_id in fact_ids
        ], key=f"{prefix}-create").json()["body"]
        statements = []

        def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", count_selects)
        try:
            response = client.post(
                f"/paam/review/v1/case/confirm/{case['id']}",
                json={
                    "expected_version": 1,
                    "idempotency_key": f"{prefix}-confirm",
                },
            )
        finally:
            event.remove(engine, "before_cursor_execute", count_selects)
        assert response.status_code == 200, response.text
        assert all("SELECT *" not in statement.upper() for statement in statements)
        return len(statements)

    one = confirmed_selects(1, "one")
    twenty = confirmed_selects(20, "twenty")
    assert twenty == one


def test_partial_refund_projection_uses_confirmed_line_allocations(target_review_api):
    client, sessions, _engine = target_review_api
    refund_id, expense_id = _facts(sessions, [("IN", 10000, "CNY"), ("OUT", 30000, "CNY")])
    case = _create(client, "REFUND", [
        {"bill_id": refund_id, "role": "REFUND_RECEIVED", "amount_value": 6000},
        {"bill_id": expense_id, "role": "REFUND_EXPENSE", "amount_value": 6000},
    ], key="partial-refund").json()["body"]
    confirmed = client.post(f"/paam/review/v1/case/confirm/{case['id']}", json={
        "expected_version": case["version"],
        "idempotency_key": "partial-refund-confirm",
    })
    assert confirmed.status_code == 200, confirmed.text
    entry = client.get("/paam/ledger/v1/entry/list").json()["items"][0]
    assert entry["allocation_status"] == "PARTIAL"
    assert entry["incoming"]["amount_value"] == 6000
    assert entry["outgoing"]["amount_value"] == 6000
    total = client.get("/paam/ledger/v1/summary").json()["totals"][0]
    assert total["refund_offset_value"] == 6000


def test_review_page_filters_old_pending_cases(target_review_api):
    client, sessions, _engine = target_review_api
    now = datetime(2026, 9, 12, 10)
    with sessions() as db:
        oldest = ReviewCase(
            review_type="FACT_CONFLICT", status="PENDING", allocation_status="CONFLICT",
            version=1, title="old pending", result_json="{}", created_time=now, updated_time=now,
        )
        db.add(oldest)
        db.flush()
        oldest_id = oldest.id
        db.add_all([ReviewCase(
            review_type="TAG", status="CONFIRMED", allocation_status="COMPLETE",
            version=1, title=f"new-{index}", result_json="{}", created_time=now, updated_time=now,
        ) for index in range(205)])
        db.commit()
    response = client.get("/paam/review/v1/case/page?status=PENDING&page_size=50")
    assert response.status_code == 200, response.text
    page = response.json()["body"]
    assert page["total"] == 1
    assert [item["id"] for item in page["items"]] == [oldest_id]
