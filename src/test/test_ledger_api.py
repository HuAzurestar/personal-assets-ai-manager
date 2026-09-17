import json
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from backend.router.dependency import get_db
from backend.router.ledger import router as ledger_router
from backend.router.ledger_account import router as ledger_account_router
from backend.router.ledger_review import router as ledger_review_router
from backend.router.ledger_review_candidate import router as ledger_review_candidate_router
from backend.router.tag import router as tag_router
from backend.router.tag_assignment import router as tag_assignment_router
from backend.entity import (
    CASH_DIRECTION_IN,
    CASH_DIRECTION_OUT,
    LedgerEntry,
    LedgerEntryTag,
    ReviewAllocation,
    ReviewCase,
    TransactionFact,
)
from backend.service.target_economic_service import TargetEconomicService
from backend.schema.target_review import TargetEconomicReviewCreateRequest
from backend.core.target_database import init_target_db


@pytest.fixture
def economic_api(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ledger-api.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False)
    init_target_db(bind=engine)
    api = FastAPI()
    api.include_router(ledger_review_router)
    api.include_router(ledger_account_router)
    api.include_router(ledger_review_candidate_router)
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


def test_review_request_normalizes_mapper_era_history_payload():
    request = TargetEconomicReviewCreateRequest.model_validate({
        "behavior_code": "TRANSFER",
        "description": "stored before router normalization",
        "entries": [{"client_key": "out", "entry_type": 1}],
        "allocations": [{
            "transaction_fact_id": 7,
            "entry_key": "out",
            "amount_value": 100,
        }],
        "idempotency_key": "stored-history",
    })

    assert request.model_dump()["title"] == "stored before router normalization"
    assert request.model_dump()["economics"] == [{
        "client_key": "out",
        "economic_type": "ACCOUNT_TRANSFER",
    }]
    assert request.model_dump()["allocations"] == [{
        "fact_id": 7,
        "economic_key": "out",
        "amount_value": 100,
    }]
def _facts(sessions, specifications):
    now = datetime(2026, 9, 13, 10)
    with sessions() as db:
        rows = [TransactionFact(
            fact_key=f"ledger-api-{uuid4().hex}",
            occurred_time=now + timedelta(minutes=index),
            cash_direction={"IN": CASH_DIRECTION_IN, "OUT": CASH_DIRECTION_OUT}[direction],
            amount_value=amount,
            amount_scale=2,
            currency_code=currency,
            account_code=f"account-{index}",
            counterparty_name="counterparty",
            counterparty_account_ref="",
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
    item = page.json()["body"]["items"][0]
    assert set(item) == {
        "id",
        "economic_type",
        "cash_direction",
        "amount",
        "account_code",
        "counterparty_account_ref",
        "projection_version",
        "occurred_time",
    }
    assert (item["economic_type"], item["cash_direction"]) == ("TRANSACTION", "OUT")
    detail = client.get(f"/paam/ledger/v1/flow/{item['id']}").json()["body"]
    assert "role" not in detail["allocations"][0]
    assert detail["flow"] == {**item, "tags": []}
    assert client.get("/paam/economy/v1/flow/list").status_code == 404
    assert client.get("/paam/economy/v1/flow/detail/1").status_code == 404
    assert client.get("/paam/economy/v1/summary").status_code == 404


def test_ledger_lists_accept_whitelisted_filter_and_sorter_objects(economic_api):
    client, sessions = economic_api
    fact_ids = _facts(sessions, [
        ("OUT", 3000, "CNY"),
        ("IN", 1000, "CNY"),
        ("IN", 2000, "USD"),
    ])
    with sessions() as db:
        TargetEconomicService(db).ensure_defaults(fact_ids, commit=True)

    response = client.get(
        "/paam/ledger/v1/flow/list",
        params={
            "filter": json.dumps({
                "op": "AND",
                "expression": [
                    {"key": "cash_direction", "op": "=", "val": "IN"},
                    {"key": "currency_code", "op": "=", "val": "CNY"},
                    {
                        "key": "occurred_time",
                        "op": "between",
                        "val": {
                            "start": "2026-09-13T00:00:00+00:00",
                            "end": "2026-09-14T00:00:00+00:00",
                        },
                    },
                ],
            }),
            "sorter": '[{"key":"amount_value","direction":"asc"}]',
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["message"] == "ok"
    assert payload["warnings"][0]["code"] == "LIST_AMOUNT_SORT_GROUPED"
    page = payload["body"]
    assert page["total"] == 1
    assert page["items"][0]["amount"]["amount_value"] == 1000
    assert set(page) == {"items", "total", "page_index", "page_size"}

    rejected = client.get(
        "/paam/ledger/v1/flow/list",
        params={
            "sorter": '[{"key":"physical_column","direction":"asc"}]',
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["body"]["code"] == "LIST_SORTER_FIELD_NOT_SUPPORTED"

    legacy = client.get(
        "/paam/ledger/v1/flow/list",
        params={"page": 1, "date_from": "2026-09-01"},
    )
    assert legacy.status_code == 422
    assert legacy.json()["body"]["code"] == "LIST_PARAMETER_NOT_SUPPORTED"

    operation = client.get("/openapi.json").json()["paths"][
        "/paam/ledger/v1/flow/list"
    ]["get"]
    assert [parameter["name"] for parameter in operation["parameters"]] == [
        "page_index",
        "page_size",
        "query",
        "filter",
        "sorter",
    ]


def test_advance_review_is_ternary_exact_and_revoke_restores_defaults(economic_api):
    client, sessions = economic_api
    fact_ids = _facts(sessions, [
        ("OUT", 50000, "CNY"),
        ("IN", 10000, "CNY"),
        ("IN", 10000, "CNY"),
        ("IN", 10000, "CNY"),
        ("IN", 10000, "CNY"),
    ])
    response = client.post("/paam/ledger/v1/review", json={
        "behavior_code": "ADVANCE",
        "title": "垫付分摊",
        "economics": [
            {"client_key": "own", "economic_type": "TRANSACTION"},
            {"client_key": "advance", "economic_type": "ACCOUNT_TRANSFER"},
            *[
                {"client_key": f"return-{index}", "economic_type": "ACCOUNT_TRANSFER"}
                for index in range(1, 5)
            ],
        ],
        "allocations": [
            {"fact_id": fact_ids[0], "economic_key": "own", "amount_value": 10000},
            {"fact_id": fact_ids[0], "economic_key": "advance", "amount_value": 40000},
            *[
                {
                    "fact_id": fact_id,
                    "economic_key": f"return-{index}",
                    "amount_value": 10000,
                }
                for index, fact_id in enumerate(fact_ids[1:], 1)
            ],
        ],
        "idempotency_key": "advance-create",
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == response.status_code
    assert response.json()["message"] == "Ledger review created"
    case = response.json()["body"]
    assert case["status"] == "PENDING"
    assert [fact["id"] for fact in case["facts"]] == fact_ids
    assert len(case["economics"]) == 6
    assert len(case["allocations"]) == 6
    with sessions() as db:
        assert db.scalar(select(func.count(LedgerEntry.id)).join(
            ReviewAllocation,
            ReviewAllocation.ledger_entry_id == LedgerEntry.id,
        ).where(ReviewAllocation.review_case_id == case["id"])) == 0
        assert list(db.scalars(select(ReviewAllocation.id).where(
            ReviewAllocation.review_case_id == case["id"],
        )).all()) == []

    confirmed = client.post(
        f"/paam/ledger/v1/review/{case['id']}/confirm",
        json={"expected_version": 1, "idempotency_key": "advance-confirm"},
    )
    assert confirmed.status_code == 200, confirmed.text
    summary_response = client.get("/paam/ledger/v1/flow/summary")
    assert summary_response.json()["status"] == summary_response.status_code
    summary = summary_response.json()["body"]
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
        assert db.scalar(select(func.count(LedgerEntry.id))) == 11
        coverage = dict(db.execute(select(
            ReviewAllocation.transaction_fact_id,
            func.sum(ReviewAllocation.amount_value),
        ).join(
            ReviewCase, ReviewCase.id == ReviewAllocation.review_case_id,
        ).join(
            LedgerEntry, LedgerEntry.id == ReviewAllocation.ledger_entry_id,
        ).where(
            ReviewCase.status == 0,
        ).group_by(ReviewAllocation.transaction_fact_id)).all())
        assert coverage == dict(zip(fact_ids, [50000, 10000, 10000, 10000, 10000]))

    revoked = client.post(
        f"/paam/ledger/v1/review/{case['id']}/revoke",
        json={"expected_version": 2, "idempotency_key": "advance-revoke"},
    )
    assert revoked.status_code == 200, revoked.text
    summary = client.get("/paam/ledger/v1/flow/summary").json()["body"]["totals"][0]
    assert summary["transaction_in_value"] == 40000
    assert summary["transaction_out_value"] == 50000
    assert summary["account_transfer_in_value"] == 0
    assert summary["account_transfer_out_value"] == 0
    with sessions() as db:
        assert db.scalar(select(func.count(LedgerEntry.id))) == 16
        assert len(list(db.scalars(select(ReviewAllocation.ledger_entry_id).where(
            ReviewAllocation.review_case_id == case["id"],
        )).all())) == 6


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
                "economic_type": "CLAIM",
            }
            for index in range(1, 3)
        ],
        *[
            {
                "client_key": f"repayment-{index}",
                "economic_type": "CLAIM",
            }
            for index in range(1, 4)
        ],
    ]
    allocations = [
        {"fact_id": fact_ids[0], "economic_key": "principal-1", "amount_value": 500000},
        {"fact_id": fact_ids[1], "economic_key": "principal-2", "amount_value": 500000},
        {"fact_id": fact_ids[2], "economic_key": "repayment-1", "amount_value": 300000},
        {"fact_id": fact_ids[3], "economic_key": "repayment-2", "amount_value": 300000},
        {"fact_id": fact_ids[4], "economic_key": "repayment-3", "amount_value": 400000},
    ]
    case = client.post("/paam/ledger/v1/review", json={
        "behavior_code": "LOAN",
        "economics": entries,
        "allocations": allocations,
        "idempotency_key": "loan-create",
    }).json()["body"]
    confirmed = client.post(
        f"/paam/ledger/v1/review/{case['id']}/confirm",
        json={"expected_version": 1, "idempotency_key": "loan-confirm"},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()["body"]
    assert sorted(item["amount_value"] for item in body["economics"]) == [
        300000, 300000, 400000, 500000, 500000,
    ]
    summary = client.get("/paam/ledger/v1/flow/summary").json()["body"]["totals"][0]
    assert summary["claim_cashflow_out_value"] == 1000000
    assert summary["claim_cashflow_in_value"] == 1000000


def test_one_economic_cannot_allocate_multiple_facts(economic_api):
    client, sessions = economic_api
    out_id, in_id = _facts(sessions, [("OUT", 12000, "CNY"), ("IN", 2000, "USD")])
    response = client.post("/paam/ledger/v1/review", json={
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
    created = client.post("/paam/ledger/v1/review", json={
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
    first = client.post(f"/paam/ledger/v1/review/{created['id']}/confirm", json=payload)
    replay = client.post(f"/paam/ledger/v1/review/{created['id']}/confirm", json=payload)
    assert first.status_code == replay.status_code == 200
    assert first.json()["body"] == replay.json()["body"]

    candidate_page = client.get("/paam/ledger/v1/review_candidate/list").json()["body"]
    candidates = candidate_page["items"]
    assert candidate_page["total"] == 1
    assert candidate_page["page_index"] == 1
    assert candidate_page["page_size"] == 20
    assert [(item["id"], item["available_value"]) for item in candidates] == [(fact_id, 6000)]
    complete_page = client.get("/paam/ledger/v1/review/list").json()["body"]
    assert complete_page["total"] == 1
    case_response = client.get("/paam/ledger/v1/review/list")
    assert case_response.json()["status"] == case_response.status_code
    assert case_response.json()["message"] == "ok"
    cases = case_response.json()["body"]
    assert set(cases) == {"items", "total", "page_index", "page_size"}
    assert cases["total"] == 1
    assert cases["items"][0]["economic_count"] == 1
    assert cases["items"][0]["allocation_count"] == 1

    filtered = client.get("/paam/ledger/v1/review/list", params={
        "filter": '{"key":"status","op":"=","val":"CONFIRMED"}',
        "sorter": '[{"key":"updated_time","direction":"desc"}]',
    })
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["body"]["total"] == 1

    legacy = client.get("/paam/ledger/v1/review/list", params={"page": 1})
    assert legacy.status_code == 422
    assert legacy.json()["body"]["code"] == "LIST_PARAMETER_NOT_SUPPORTED"

    unsupported_query = client.get("/paam/ledger/v1/review/list", params={
        "query": '[{"key":"title","word":"purchase"}]',
    })
    assert unsupported_query.status_code == 422
    assert unsupported_query.json()["body"]["code"] == "LIST_QUERY_NOT_SUPPORTED"

    with sessions() as db:
        coverage = db.scalar(select(func.sum(ReviewAllocation.amount_value)).join(
            ReviewCase, ReviewCase.id == ReviewAllocation.review_case_id,
        ).join(
            LedgerEntry, LedgerEntry.id == ReviewAllocation.ledger_entry_id,
        ).where(
            ReviewAllocation.transaction_fact_id == fact_id,
            ReviewCase.status == 0,
        ))
        assert coverage == 10000


def test_review_candidate_list_is_paged_with_fixed_query_count(economic_api):
    client, sessions = economic_api
    fact_ids = _facts(sessions, [
        ("OUT", 1000, "CNY"),
        ("OUT", 2000, "CNY"),
        ("OUT", 3000, "CNY"),
    ])
    with sessions() as db:
        TargetEconomicService(db).ensure_defaults(fact_ids, commit=True)

    statements = []

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        first = client.get(
            "/paam/ledger/v1/review_candidate/list?page_index=1&page_size=2"
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == first.status_code
    assert first.json()["message"] == "ok"
    page = first.json()["body"]
    assert page["total"] == 3
    assert page["page_index"] == 1
    assert page["page_size"] == 2
    assert len(page["items"]) == 2
    assert len(statements) == 2

    second = client.get(
        "/paam/ledger/v1/review_candidate/list?page_index=2&page_size=2"
    ).json()["body"]
    assert second["total"] == 3
    assert len(second["items"]) == 1


def test_review_list_is_database_paged_with_fixed_query_count(economic_api):
    client, sessions = economic_api
    fact_ids = _facts(sessions, [
        ("OUT", 1000 + index, "CNY") for index in range(30)
    ])
    with sessions() as db:
        TargetEconomicService(db).ensure_defaults(fact_ids, commit=True)
        now = datetime(2026, 9, 14, 10)
        db.add_all([
            ReviewCase(
                behavior_type=0,
                status=1,
                title=f"manual review {index}",
                created_time=now + timedelta(minutes=index),
                updated_time=now + timedelta(minutes=index),
            )
            for index in range(30)
        ])
        db.commit()

    statements = []

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        response = client.get(
            "/paam/ledger/v1/review/list",
            params={
                "page_index": 2,
                "page_size": 10,
                "sorter": '[{"key":"created_time","direction":"asc"}]',
            },
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    assert response.status_code == 200, response.text
    page = response.json()["body"]
    assert page["total"] == 30
    assert page["page_index"] == 2
    assert len(page["items"]) == 10
    assert len(statements) == 2


def test_review_candidate_list_supports_declared_filter_contract(economic_api):
    client, sessions = economic_api
    fact_ids = _facts(sessions, [
        ("OUT", 1000, "CNY"),
        ("IN", 2000, "USD"),
    ])
    with sessions() as db:
        TargetEconomicService(db).ensure_defaults(fact_ids, commit=True)

    response = client.get("/paam/ledger/v1/review_candidate/list", params={
        "filter": '{"op":"AND","expression":[{"key":"cash_direction","op":"=","val":"IN"},{"key":"currency_code","op":"=","val":"usd"}]}',
        "sorter": '[{"key":"occurred_time","direction":"asc"}]',
    })
    assert response.status_code == 200, response.text
    body = response.json()["body"]
    assert [item["id"] for item in body["items"]] == [fact_ids[1]]
    assert set(body) == {"items", "total", "page_index", "page_size"}


def test_fx_review_uses_two_single_currency_account_transfers(economic_api):
    client, sessions = economic_api
    cny_id, usd_id = _facts(sessions, [("OUT", 12000, "CNY"), ("IN", 2000, "USD")])
    case = client.post("/paam/ledger/v1/review", json={
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
    response = client.post(f"/paam/ledger/v1/review/{case['id']}/confirm", json={
        "expected_version": 1,
        "idempotency_key": "fx-confirm",
    })
    assert response.status_code == 200, response.text
    flows = response.json()["body"]["economics"]
    assert {(item["cash_direction"], item["amount_value"], item["currency_code"]) for item in flows} == {
        ("OUT", 12000, "CNY"),
        ("IN", 2000, "USD"),
    }
    totals = client.get(
        "/paam/ledger/v1/flow/summary"
    ).json()["body"]["totals"]
    assert {(item["currency_code"], item["account_transfer_in_value"], item["account_transfer_out_value"]) for item in totals} == {
        ("CNY", 0, 12000),
        ("USD", 2000, 0),
    }


def test_ledger_review_rejects_removed_fields(economic_api):
    client, sessions = economic_api
    fact_id = _facts(sessions, [("IN", 4000, "CNY")])[0]
    rejected = client.post("/paam/ledger/v1/review", json={
        "behavior_code": "REFUND",
        "title": "legacy title",
        "economics": [{
            "client_key": "refund",
            "economic_type": "TRANSACTION",
            "reversal_of_id": 1,
        }],
        "allocations": [{
            "fact_id": fact_id,
            "economic_key": "refund",
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
    case = client.post("/paam/ledger/v1/review", json={
        "behavior_code": "SPLIT_PURCHASE",
        "economics": [
            {"client_key": "goods", "economic_type": "TRANSACTION"},
            {"client_key": "service", "economic_type": "TRANSACTION"},
        ],
        "allocations": [
            {"fact_id": fact_id, "economic_key": "goods", "amount_value": 6000},
            {"fact_id": fact_id, "economic_key": "service", "amount_value": 4000},
        ],
        "idempotency_key": "split-tag-create",
    }).json()["body"]
    confirmed = client.post(f"/paam/ledger/v1/review/{case['id']}/confirm", json={
        "expected_version": 1,
        "idempotency_key": "split-tag-confirm",
    })
    assert confirmed.status_code == 200, confirmed.text
    ledger_ids = [row["id"] for row in confirmed.json()["body"]["economics"]]
    with sessions() as db:
        assert db.query(LedgerEntryTag).count() == 2

    tagged_view = client.post(f"/paam/tag/v1/view/{view['id']}/tag", json={
        "name": "Food",
        "system_name": "food",
    })
    assert tagged_view.status_code == 200, tagged_view.text
    detail = client.get(f"/paam/ledger/v1/flow/{ledger_ids[0]}").json()["body"]
    assigned = client.put(f"/paam/tag/v1/assignment/{ledger_ids[0]}", json={
        "tag_state": {"category": "food"},
        "expected_projection_version": detail["flow"]["projection_version"],
    })
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["body"]["tag_state"] == {"category": "food"}
    details = [
        client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()["body"]
        for ledger_id in ledger_ids
    ]
    assert details[0]["flow"]["tags"][0]["tag_system_name"] == "food"
    assert details[1]["flow"]["tags"][0]["tag_system_name"] == "unclassified"

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


def test_ledger_account_update_changes_only_selected_split_ledger(economic_api):
    client, sessions = economic_api
    fact_id = _facts(sessions, [("OUT", 10000, "CNY")])[0]
    with sessions() as db:
        TargetEconomicService(db).ensure_defaults([fact_id], commit=True)
    case = client.post("/paam/ledger/v1/review", json={
        "behavior_code": "SPLIT_PURCHASE",
        "economics": [
            {"client_key": "goods", "economic_type": "TRANSACTION"},
            {"client_key": "service", "economic_type": "TRANSACTION"},
        ],
        "allocations": [
            {"fact_id": fact_id, "economic_key": "goods", "amount_value": 6000},
            {"fact_id": fact_id, "economic_key": "service", "amount_value": 4000},
        ],
        "idempotency_key": "split-account-create",
    }).json()["body"]
    confirmed = client.post(f"/paam/ledger/v1/review/{case['id']}/confirm", json={
        "expected_version": 1,
        "idempotency_key": "split-account-confirm",
    })
    assert confirmed.status_code == 200, confirmed.text
    ledger_ids = [row["id"] for row in confirmed.json()["body"]["economics"]]
    selected_ledger_id = ledger_ids[0]
    account = client.get(
        f"/paam/ledger/v1/flow/{selected_ledger_id}/account"
    ).json()["body"]
    corrected = client.put(f"/paam/ledger/v1/flow/{selected_ledger_id}/account", json={
        "account_code": "checked-bank",
        "expected_projection_version": account["projection_version"],
    })
    assert corrected.status_code == 200, corrected.text
    details = [
        client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()["body"]
        for ledger_id in ledger_ids
    ]
    assert details[0]["flow"]["account_code"] == "checked-bank"
    assert details[1]["flow"]["account_code"] == "account-1"
    assert all(item["facts"][0]["account_code"] == "account-1" for item in details)
    assert all(
        {review["review_type"] for review in item["reviews"]}
        == {"SPLIT_PURCHASE"}
        for item in details
    )
    with sessions() as db:
        assert db.get(TransactionFact, fact_id).account_code == "account-1"

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
        "occurred_time",
        "created_time",
        "updated_time",
    }
