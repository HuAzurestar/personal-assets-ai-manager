from datetime import datetime, timedelta, timezone
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


def test_review_request_rejects_mapper_era_history_payload():
    with pytest.raises(ValueError):
        TargetEconomicReviewCreateRequest.model_validate({
        "behavior_code": "TRANSFER",
        "description": "stored before router normalization",
        "entries": [{"client_key": "out", "entry_type": 1}],
        "allocations": [{
            "transaction_fact_id": 7,
            "entry_key": "out",
            "amount": 100,
        }],
        "idempotency_key": "stored-history",
        })
def _facts(sessions, specifications):
    now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    with sessions() as db:
        rows = [TransactionFact(
            fact_key=f"ledger-api-{uuid4().hex}",
            occurred_time=now + timedelta(minutes=index),
            cash_direction={"IN": CASH_DIRECTION_IN, "OUT": CASH_DIRECTION_OUT}[direction],
            amount=amount,
            currency_code=currency,
            account_code=f"account-{index}",
            counterparty_name="counterparty",
            counterparty_account_ref="",
            summary="fact",
            created_time=now,
            updated_time=now,
        ) for index, (direction, amount, currency) in enumerate(specifications, 1)]
        db.add_all(rows)
        db.flush()
        from backend.mapper.review_command_mapper import ReviewCommandMapper
        ReviewCommandMapper(db).create_initial_defaults([row.id for row in rows])
        db.commit()
        return [row.id for row in rows]


def test_ledger_v1_exposes_only_confirmed_cash_entry_fields(economic_api):
    client, sessions = economic_api
    fact_id = _facts(sessions, [("OUT", 12345, "CNY")])[0]
    with sessions() as db:
        db.get(TransactionFact, fact_id).summary = ""
        TargetEconomicService(db).ensure_defaults([fact_id], commit=True)

    page = client.get("/paam/ledger/v1/flow/list")
    assert page.status_code == 200, page.text
    item = page.json()["body"]["items"][0]
    assert set(item) == {
        "id",
        "active",
        "summary",
        "review_behavior_type",
        "entry_type",
        "entry_direction",
        "amount",
        "currency_code",
        "account_code",
        "counterparty_account_ref",
        "occurred_time",
        "created_time",
        "updated_time",
        "tags",
    }
    assert item["tags"] == []
    assert item["active"] is True
    assert item["summary"] == "事实交易"
    assert item["review_behavior_type"] == 0
    assert (item["entry_type"], item["entry_direction"]) == (0, CASH_DIRECTION_OUT)
    detail = client.get(f"/paam/ledger/v1/flow/{item['id']}").json()["body"]
    assert "role" not in detail["allocations"][0]
    assert detail["ledger_entry"] == item
    assert client.get("/paam/economy/v1/flow/list").status_code == 404
    assert client.get("/paam/economy/v1/flow/detail/1").status_code == 404
    assert client.get("/paam/economy/v1/summary").status_code == 404


def test_ledger_summary_uses_the_selected_timezone_day_boundary(economic_api):
    client, sessions = economic_api
    created = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with sessions() as db:
        facts = [
            TransactionFact(
                fact_key=f"summary-boundary-{index}",
                occurred_time=occurred_time,
                cash_direction=CASH_DIRECTION_IN,
                amount=amount,
                currency_code="CNY",
                account_code="wallet",
                counterparty_name="boundary",
                counterparty_account_ref="",
                summary="boundary",
                created_time=created,
                updated_time=created,
            )
            for index, (occurred_time, amount) in enumerate((
                (datetime(2026, 8, 31, 15, 59, tzinfo=timezone.utc), 100),
                (datetime(2026, 8, 31, 16, 0, tzinfo=timezone.utc), 200),
            ))
        ]
        db.add_all(facts)
        db.flush()
        TargetEconomicService(db).ensure_defaults([fact.id for fact in facts])
        db.commit()

    response = client.get(
        "/paam/ledger/v1/flow/summary",
        params={
            "date_from": "2026-09-01",
            "date_to": "2026-09-01",
            "timezone": "Asia/Hong_Kong",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()["body"]
    assert body["entry_count"] == 1
    assert body["totals"][0]["income_and_expense_in_amount"] == 200
    assert body["trend"][0]["day"] == "2026-09-01"


def test_ledger_lists_accept_whitelisted_filter_and_sorter_objects(economic_api):
    client, sessions = economic_api
    fact_ids = _facts(sessions, [
        ("OUT", 3000, "CNY"),
        ("IN", 1000, "CNY"),
        ("IN", 2000, "USD"),
    ])
    with sessions() as db:
        TargetEconomicService(db).ensure_defaults(fact_ids, commit=True)

    page = client.get(
        "/paam/ledger/v1/flow/list",
        params={
            "filter": '{"op":"AND","expression":[{"key":"entry_direction","op":"=","val":1},{"key":"currency_code","op":"=","val":"CNY"}]}',
            "sorter": '[{"key":"amount","direction":"asc"}]',
        },
    ).json()["body"]
    assert page["total"] == 1
    assert page["items"][0]["amount"] == 1000
    assert page["items"][0]["summary"] == "事实交易：fact"
    assert set(page) == {"items", "total", "page_index", "page_size"}

    rejected = client.get(
        "/paam/ledger/v1/flow/list",
        params={"sorter": '[{"key":"physical_column","direction":"asc"}]'},
    )
    assert rejected.status_code == 422
    assert rejected.json()["body"]["code"] == "LIST_SORTER_FIELD_NOT_SUPPORTED"

    rejected_active = client.get(
        "/paam/ledger/v1/flow/list",
        params={"filter": '{"key":"active","op":"=","val":1}'},
    )
    assert rejected_active.status_code == 422
    assert rejected_active.json()["body"]["code"] == "LIST_FILTER_VALUE_INVALID"


def test_ledger_list_loads_sparse_tags_with_fixed_query_count(economic_api):
    client, sessions = economic_api
    fact_ids = _facts(sessions, [("OUT", 1000 + index, "CNY") for index in range(30)])
    with sessions() as db:
        TargetEconomicService(db).ensure_defaults(fact_ids, commit=True)

    statements = []

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        response = client.get("/paam/ledger/v1/flow/list?page_size=20")
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    assert response.status_code == 200, response.text
    assert len(response.json()["body"]["items"]) == 20
    # One snapshot integrity probe plus count, page and sparse-tag load.
    assert len(statements) == 4


def test_advance_review_is_ternary_exact_and_revoke_restores_defaults(economic_api):
    client, sessions = economic_api
    ids = _facts(sessions, [("OUT", 50000, "CNY"), *[("IN", 10000, "CNY")] * 4])
    _mock_party(sessions)
    advance = _review(client, new_reviews=[dict(case_code="SHARED_PAYMENT", title="Mock shared advance", parameters=dict(
        phase="ADVANCE_OUT", new_positions=[_mock_position()], allocations=[_split(ids[0], 10000), _split(ids[0], 40000, "ASSET_LIABILITY")],
        legs=[_leg(40000, "IN", new_position_index=0)],
        position_allocations=[dict(allocation_index=1, leg_index=0, cash_amount=40000, cash_currency_code="CNY")]))])
    aid = advance["created_reviews"][0]["id"]
    pid = advance["created_positions"][0]["id"]
    source = client.get(f"/paam/ledger/v1/review/{aid}").json()["body"]["position_legs"][0]["id"]
    collection = _review(client, new_reviews=[dict(case_code="SHARED_PAYMENT", title="Mock collections", parameters=dict(
        phase="COLLECT_IN", allocations=[_split(fid, 10000, "ASSET_LIABILITY") for fid in ids[1:]],
        legs=[_leg(10000, "OUT", existing_position_id=pid, source=source) for _ in ids[1:]],
        position_allocations=[dict(allocation_index=i, leg_index=i, cash_amount=10000, cash_currency_code="CNY") for i in range(4)]))])
    cid = collection["created_reviews"][0]["id"]
    summary = client.get("/paam/ledger/v1/flow/summary").json()["body"]["totals"][0]
    assert summary["income_and_expense_out_amount"] == 10000
    assert summary["asset_and_liability_in_amount"] == summary["asset_and_liability_out_amount"] == 40000
    with sessions() as db:
        assert db.scalar(select(func.count(LedgerEntry.id))) == 11
        coverage = dict(db.execute(select(ReviewAllocation.transaction_fact_id, func.sum(ReviewAllocation.amount)).join(
            ReviewCase, ReviewCase.id == ReviewAllocation.review_case_id).where(ReviewCase.status == 0).group_by(
            ReviewAllocation.transaction_fact_id)).all())
        assert coverage == dict(zip(ids, [50000, 10000, 10000, 10000, 10000]))
    original = client.get(f"/paam/ledger/v1/review/{aid}").json()["body"]
    _review(client, deactivate_review_ids=[aid, cid])
    revoked = client.get(f"/paam/ledger/v1/review/{aid}").json()["body"]
    assert revoked["status"] == "REVOKED"
    assert revoked["ledger_entries"] == original["ledger_entries"]
    summary = client.get("/paam/ledger/v1/flow/summary").json()["body"]["totals"][0]
    assert summary["income_and_expense_in_amount"] == 40000
    assert summary["income_and_expense_out_amount"] == 50000
    assert summary["asset_and_liability_in_amount"] == summary["asset_and_liability_out_amount"] == 0
    with sessions() as db:
        assert db.scalar(select(func.count(LedgerEntry.id))) == 11


def test_loan_uses_one_asset_liability_cashflow_entry_per_fact(economic_api):
    client, sessions = economic_api
    ids = _facts(sessions, [("OUT", 500000, "CNY"), ("OUT", 500000, "CNY"),
                            ("IN", 300000, "CNY"), ("IN", 300000, "CNY"), ("IN", 400000, "CNY")])
    _mock_party(sessions)
    start = _review(client, new_reviews=[dict(case_code="BORROW_REPAY", parameters=dict(
        new_positions=[_mock_position(), _mock_position()], allocations=[_split(fid, 500000, "ASSET_LIABILITY") for fid in ids[:2]],
        legs=[_leg(500000, "IN", new_position_index=i) for i in range(2)],
        position_allocations=[dict(allocation_index=i, leg_index=i, cash_amount=500000, cash_currency_code="CNY") for i in range(2)]))])
    original = client.get(f"/paam/ledger/v1/review/{start['created_reviews'][0]['id']}").json()["body"]
    pids = [row["id"] for row in start["created_positions"]]
    sources = [row["id"] for row in original["position_legs"]]
    repayments = _review(client, new_reviews=[dict(case_code="BORROW_REPAY", parameters=dict(
        allocations=[_split(fid, amount, "ASSET_LIABILITY") for fid, amount in zip(ids[2:], [300000, 300000, 400000])],
        legs=[_leg(amount, "OUT", existing_position_id=pids[pi], source=sources[pi]) for pi, amount in [(0,300000),(1,300000),(0,200000),(1,200000)]],
        position_allocations=[dict(allocation_index=ai, leg_index=li, cash_amount=amount, cash_currency_code="CNY")
                              for ai,li,amount in [(0,0,300000),(1,1,300000),(2,2,200000),(2,3,200000)]]))])
    case = client.get(f"/paam/ledger/v1/review/{repayments['created_reviews'][0]['id']}").json()["body"]
    assert sorted(row["cash_amount"] for row in original["ledger_entries"] + case["ledger_entries"]) == [300000,300000,400000,500000,500000]
    summary = client.get("/paam/ledger/v1/flow/summary").json()["body"]["totals"][0]
    assert summary["asset_and_liability_out_amount"] == summary["asset_and_liability_in_amount"] == 1000000


def test_one_economic_cannot_allocate_multiple_facts(economic_api):
    client, sessions = economic_api
    out_id, in_id = _facts(sessions, [("OUT", 12000, "CNY"), ("IN", 2000, "USD")])
    response = client.post("/paam/ledger/v1/review/preview", json=dict(new_reviews=[dict(case_code="NORMAL",
        parameters=dict(allocations=[_split(out_id, 12000) | {"ledger_id": 1}, _split(in_id, 2000)]))]))
    assert response.status_code == 422
    case = _review(client, new_reviews=[dict(case_code="INTERNAL_TRANSFER", parameters=dict(transaction_ids=[out_id, in_id]))])
    detail = client.get(f"/paam/ledger/v1/review/{case['created_reviews'][0]['id']}").json()["body"]
    assert len({row["ledger_id"] for row in detail["allocations"]}) == 2


def test_partial_manual_reviews_require_full_intent_and_stale_commands_do_not_replay(economic_api):
    client, sessions = economic_api
    fid = _facts(sessions, [("OUT", 10000, "CNY")])[0]
    partial = dict(new_reviews=[dict(case_code="NORMAL", parameters=dict(allocations=[_split(fid, 4000)]))])
    preview = client.post("/paam/ledger/v1/review/preview", json=partial).json()["body"]
    assert preview["blocking_issues"][0]["code"] == "FACT_COVERAGE_REQUIRED"
    full = dict(new_reviews=[dict(case_code="NORMAL", parameters=dict(allocations=[_split(fid,4000), _split(fid,6000)]))])
    preview = client.post("/paam/ledger/v1/review/preview", json=full).json()["body"]
    payload = full | dict(expected_reviews=preview["expected_reviews"], preview_digest=preview["preview_digest"])
    first = client.post("/paam/ledger/v1/review/command", json=payload)
    assert first.status_code == 200, first.text
    assert client.post("/paam/ledger/v1/review/command", json=payload).status_code == 409
    with sessions() as db:
        assert db.scalar(select(func.count(ReviewCase.id))) == 2
        coverage = db.scalar(select(func.sum(ReviewAllocation.amount)).join(
            ReviewCase, ReviewCase.id == ReviewAllocation.review_case_id).where(
            ReviewAllocation.transaction_fact_id == fid, ReviewCase.status == 0))
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
                "filter": '{"key":"behavior_type","op":"=","val":0}',
            },
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    assert response.status_code == 200, response.text
    page = response.json()["body"]
    assert page["total"] == 30
    assert len(page["items"]) == 10
    assert len(statements) == 2


def test_review_candidate_list_supports_shared_query_contract(economic_api):
    client, sessions = economic_api
    fact_ids = _facts(sessions, [
        ("OUT", 1000, "CNY"),
        ("IN", 2000, "USD"),
    ])
    with sessions() as db:
        TargetEconomicService(db).ensure_defaults(fact_ids, commit=True)

    response = client.get("/paam/ledger/v1/review_candidate/list", params={
        "filter": '{"op":"AND","expression":[{"key":"cash_direction","op":"=","val":1},{"key":"currency_code","op":"=","val":"USD"}]}',
        "sorter": '[{"key":"occurred_time","direction":"asc"}]',
    })
    assert response.status_code == 200, response.text
    body = response.json()["body"]
    assert [item["id"] for item in body["items"]] == [fact_ids[1]]
    assert set(body) == {"items", "total", "page_index", "page_size"}

    ranged = client.get("/paam/ledger/v1/review_candidate/list", params={
        "filter": '{"op":"AND","expression":[{"key":"occurred_time","op":">=","val":"2026-09-13T10:02:00+00:00"},{"key":"occurred_time","op":"<","val":"2026-09-13T10:03:00+00:00"}]}',
    })
    assert ranged.status_code == 200, ranged.text
    assert [item["id"] for item in ranged.json()["body"]["items"]] == [fact_ids[1]]


def test_fx_review_uses_two_single_currency_account_transfers(economic_api):
    client, sessions = economic_api
    ids = _facts(sessions, [("OUT", 12000, "CNY"), ("IN", 2000, "USD")])
    result = _review(client, new_reviews=[dict(case_code="INTERNAL_TRANSFER", parameters=dict(transaction_ids=ids))])
    case = client.get(f"/paam/ledger/v1/review/{result['created_reviews'][0]['id']}").json()["body"]
    assert {(row["cash_direction"], row["cash_amount"], row["cash_currency_code"]) for row in case["ledger_entries"]} == {("OUT",12000,"CNY"),("IN",2000,"USD")}
    totals = client.get("/paam/ledger/v1/flow/summary").json()["body"]["totals"]
    assert {(row["currency_code"], row["internal_transfer_in_amount"], row["internal_transfer_out_amount"]) for row in totals} == {("CNY",0,12000),("USD",2000,0)}


def test_ledger_review_rejects_removed_fields(economic_api):
    client, sessions = economic_api
    fid = _facts(sessions, [("IN",4000,"CNY")])[0]
    response = client.post("/paam/ledger/v1/review/preview", json=dict(new_reviews=[dict(case_code="REFUND",
        parameters=dict(allocations=[_split(fid,4000) | {"reversal_of_id": 1}]))]))
    assert response.status_code == 422
    assert client.post("/paam/ledger/v1/review", json={}).status_code == 410


def test_tag_sync_uses_allocations_for_every_split_ledger_entry(economic_api):
    client, sessions = economic_api
    fact_id = _facts(sessions, [("OUT", 10000, "CNY")])[0]
    with sessions() as db:
        TargetEconomicService(db).ensure_defaults([fact_id], commit=True)
    view = client.post("/paam/tag/v1/view", json={
        "name": "Category",
        "system_name": "category",
    }).json()["body"]
    result = _review(client, new_reviews=[dict(case_code="NORMAL", parameters=dict(allocations=[_split(fact_id,6000), _split(fact_id,4000)]))])
    case = client.get(f"/paam/ledger/v1/review/{result['created_reviews'][0]['id']}").json()["body"]
    ledger_ids = [row["id"] for row in case["ledger_entries"]]
    with sessions() as db:
        assert db.query(LedgerEntryTag).count() == 3

    tagged_view = client.post(f"/paam/tag/v1/view/{view['id']}/tag", json={
        "name": "Food",
        "system_name": "food",
    })
    assert tagged_view.status_code == 200, tagged_view.text
    detail = client.get(f"/paam/ledger/v1/flow/{ledger_ids[0]}").json()["body"]
    assignment = client.get(
        f"/paam/tag/v1/assignment/{ledger_ids[0]}"
    ).json()["body"]
    assigned = client.put(f"/paam/tag/v1/assignment/{ledger_ids[0]}", json={
        "expected_updated_time": assignment["updated_time"],
        "tag_state": {"category": "food"},
    })
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["body"]["tag_state"] == {"category": "food"}
    details = [
        client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()["body"]
        for ledger_id in ledger_ids
    ]
    assert details[0]["ledger_entry"]["tags"][0]["tag_system_name"] == "food"
    assert details[1]["ledger_entry"]["tags"][0]["tag_system_name"] == "unclassified"

    archived = client.put(f"/paam/tag/v1/view/{view['id']}", json={
        "status": "ARCHIVED",
    })
    assert archived.status_code == 200, archived.text
    with sessions() as db:
        assert db.query(LedgerEntryTag).count() == 1  # stopped default retains its original tag

    restored = client.put(f"/paam/tag/v1/view/{view['id']}", json={
        "status": "ACTIVE",
    })
    assert restored.status_code == 200, restored.text
    with sessions() as db:
        assert db.query(LedgerEntryTag).count() == 3


def test_ledger_account_requires_replacement_and_keeps_original_split_content(economic_api):
    client, sessions = economic_api
    fid = _facts(sessions, [("OUT",10000,"CNY")])[0]
    original = _review(client, new_reviews=[dict(case_code="NORMAL", parameters=dict(allocations=[_split(fid,6000), _split(fid,4000)]))])
    rid = original["created_reviews"][0]["id"]
    case = client.get(f"/paam/ledger/v1/review/{rid}").json()["body"]
    ledger_ids = [row["id"] for row in case["ledger_entries"]]
    assert client.put(f"/paam/ledger/v1/flow/{ledger_ids[0]}/account", json={"account_code": "changed"}).status_code == 410
    with sessions() as db:
        from backend.entity import LedgerAccountRef
        db.add(LedgerAccountRef(id=1, account_id=0, name="Mock corrected source", institution="", reference="", source_namespace="",
            source_identity="", identity_strength=0, status="ACTIVE"))
        db.commit()
    replacement = _review(client, new_reviews=[dict(case_code="NORMAL", parameters=dict(
        allocations=[_split(fid,6000) | {"account_ref_id": 1}, _split(fid,4000)]))])
    new_case = client.get(f"/paam/ledger/v1/review/{replacement['created_reviews'][0]['id']}").json()["body"]
    assert [row["account_ref_id"] for row in new_case["ledger_entries"]] == [1,0]
    revoked = client.get(f"/paam/ledger/v1/review/{rid}").json()["body"]
    assert revoked["ledger_entries"] == case["ledger_entries"]
    with sessions() as db:
        assert db.get(TransactionFact, fid).account_code == "account-1"


def test_ledger_entry_entity_has_only_cash_projection_columns():
    assert set(LedgerEntry.__table__.columns.keys()) == {
        "id",
        "entry_type",
        "entry_direction",
        "cash_amount",
        "cash_currency_code",
        "account_ref_id",
        "account_code",
        "counterparty_account_ref",
        "occurred_time",
        "created_time",
        "updated_time",
    }

def _review(client, **intent):
    response = client.post("/paam/ledger/v1/review/preview", json=intent)
    assert response.status_code == 200, response.text
    preview = response.json()["body"]
    assert not preview["blocking_issues"], preview
    response = client.post("/paam/ledger/v1/review/command", json=intent | dict(
        expected_reviews=preview["expected_reviews"], preview_digest=preview["preview_digest"]))
    assert response.status_code == 200, response.text
    return response.json()["body"]


def _split(fid, amount, economic_type="TRANSACTION"):
    return dict(transaction_id=fid, cash_amount=amount, economic_type=economic_type, account_ref_id=0)


def _mock_party(sessions):
    from backend.entity import LedgerAccountParty
    with sessions() as db:
        db.add(LedgerAccountParty(id=1, name="Mock person", status="ACTIVE"))
        db.commit()


def _mock_position():
    return dict(title="Mock note", description="", type="ASSET", usage_scenario="SHARED-SETTLEMENT",
                party_id=1, counterparty="Mock counterpart", unit_code="CNY")


def _leg(amount, direction, **target):
    return dict(type="MOVEMENT", leg_amount=amount, leg_direction=direction,
                occurred_time="2026-09-13T10:02:00Z", **target)
