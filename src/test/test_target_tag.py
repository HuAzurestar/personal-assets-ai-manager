from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from backend.core.target_database import init_target_db
from backend.entity import (
    CASH_DIRECTION_OUT,
    LedgerEntryTag,
    ReviewAllocation,
    ReviewCase,
    TargetTag,
    TargetTagView,
    TransactionFact,
)
from backend.router.dependency import get_db
from backend.router.ledger import router as ledger_router
from backend.router.ledger_account import router as ledger_account_router
from backend.router.tag import router as tag_router
from backend.router.tag_assignment import router as tag_assignment_router
from backend.service.target_economic_service import TargetEconomicService


@pytest.fixture
def target_tag_api(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'target-tag.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False)
    init_target_db(bind=engine)
    api = FastAPI()
    api.include_router(tag_router)
    api.include_router(tag_assignment_router)
    api.include_router(ledger_router)
    api.include_router(ledger_account_router)

    def override_db():
        with sessions() as db:
            yield db

    api.dependency_overrides[get_db] = override_db
    with TestClient(api) as client:
        yield client, sessions, engine
    engine.dispose()


def _add_facts(sessions, count):
    now = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
    with sessions() as db:
        facts = [
            TransactionFact(
                fact_key=uuid4().hex,
                occurred_time=now + timedelta(minutes=index),
                cash_direction=CASH_DIRECTION_OUT,
                amount=1000,
                currency_code="CNY",
                account_code="wallet",
                counterparty_name="merchant",
                counterparty_account_ref="",
                summary="expense",
                created_time=now,
                updated_time=now,
            )
            for index in range(count)
        ]
        db.add_all(facts)
        db.flush()
        fact_ids = [fact.id for fact in facts]
        TargetEconomicService(db).ensure_defaults(fact_ids, commit=True)
        return fact_ids


def _ledger_for_fact(sessions, fact_id):
    with sessions() as db:
        return db.scalar(
            select(ReviewAllocation.ledger_entry_id)
            .join(ReviewCase, ReviewCase.id == ReviewAllocation.review_case_id)
            .where(
                ReviewAllocation.transaction_fact_id == fact_id,
                ReviewAllocation.ledger_entry_id > 0,
                ReviewCase.status == 0,
            )
        )


def _create_tag_dictionary(client):
    view = client.post(
        "/paam/tag/v1/view",
        json={"name": "Category", "system_name": "category"},
    ).json()["body"]
    response = client.post(
        f"/paam/tag/v1/view/{view['id']}/tag",
        json={"name": "Food", "system_name": "food"},
    )
    assert response.status_code == 200, response.text
    return response.json()["body"]


def _assign(client, ledger_id, value):
    return client.put(
        f"/paam/tag/v1/assignment/{ledger_id}",
        json={
            "tag_state": {"category": value},
        },
    )


@pytest.mark.parametrize(
    ("display_name", "system_name"),
    [
        ("消费 类型", "xiao_fei_lei_xing"),
        ("Monthly Budget", "monthly_budget"),
        ("现金 Cash Account", "xian_jin_cash_account"),
        ("2026 年预算", "tag_2026_nian_yu_suan"),
    ],
)
def test_target_tag_system_name_preview(target_tag_api, display_name, system_name):
    client, _sessions, _engine = target_tag_api
    response = client.post(
        "/paam/tag/v1/system_name/preview",
        json={"name": display_name},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": 200,
        "message": "ok",
        "body": {"system_name": system_name},
    }


def test_target_tag_dictionary_assigns_one_default_per_active_view(target_tag_api):
    client, sessions, _engine = target_tag_api
    _add_facts(sessions, 2)
    view = _create_tag_dictionary(client)
    assert [(tag["name"], tag["system_name"]) for tag in view["tags"]] == [
        ("未分类", "unclassified"),
        ("Food", "food"),
    ]
    with sessions() as db:
        assert db.query(LedgerEntryTag).count() == 2

    _add_facts(sessions, 1)
    with sessions() as db:
        assert db.query(LedgerEntryTag).count() == 3

    assert client.put(
        f"/paam/tag/v1/view/{view['id']}", json={"status": "ARCHIVED"}
    ).status_code == 200
    archived = client.get("/paam/tag/v1/view/list").json()["body"]["items"]
    assert [(item["id"], item["status"]) for item in archived] == [
        (view["id"], "ARCHIVED")
    ]
    active = client.get("/paam/tag/v1/view/list", params={
        "filter": '{"key":"status","op":"=","val":"ACTIVE"}',
    }).json()["body"]
    assert active["items"] == []
    assert client.put(
        f"/paam/tag/v1/view/{view['id']}", json={"status": "ACTIVE"}
    ).status_code == 200
    with sessions() as db:
        assert db.query(LedgerEntryTag).count() == 3
        assert db.query(TargetTagView).count() == 1
        assert db.query(TargetTag).count() == 2


def test_target_tag_list_query_count_is_fixed(target_tag_api):
    client, _sessions, engine = target_tag_api
    last_view_id = 0
    for index in range(20):
        response = client.post(
            "/paam/tag/v1/view",
            json={"name": f"View {index}", "system_name": f"view_{index}"},
        )
        assert response.status_code == 200, response.text
        last_view_id = response.json()["body"]["id"]

    statements = []

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        response = client.get("/paam/tag/v1/view/list")
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    body = response.json()["body"]
    assert (body["total"], body["page_index"], body["page_size"]) == (20, 1, 20)
    assert len(body["items"]) == 20
    assert len(statements) == 3
    assert all("SELECT *" not in statement.upper() for statement in statements)

    filtered = client.get(
        "/paam/tag/v1/view/list",
        params={
            "filter": f'{{"key":"id","op":"=","val":{last_view_id}}}',
            "sorter": '[{"key":"created_time","direction":"desc"}]',
        },
    ).json()["body"]
    assert filtered["total"] == 1
    assert filtered["items"][0]["system_name"] == "view_19"
    assert set(filtered) == {"items", "total", "page_index", "page_size"}

    rejected = client.get(
        "/paam/tag/v1/view/list",
        params={"sorter": '[{"key":"view_id","direction":"asc"}]'},
    )
    assert rejected.status_code == 422
    assert rejected.json()["body"]["code"] == "LIST_SORTER_FIELD_NOT_SUPPORTED"

    legacy = client.get(
        "/paam/tag/v1/view/list",
        params={"include_archived": "true"},
    )
    assert legacy.status_code == 422
    assert legacy.json()["body"]["code"] == "LIST_PARAMETER_NOT_SUPPORTED"


def test_ledger_tag_assignment_is_direct_and_idempotent(target_tag_api):
    client, sessions, _engine = target_tag_api
    fact_id = _add_facts(sessions, 1)[0]
    _create_tag_dictionary(client)
    ledger_id = _ledger_for_fact(sessions, fact_id)

    assigned = _assign(client, ledger_id, "food")
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["body"] == {
        "ledger_id": ledger_id,
        "tag_state": {"category": "food"},
    }
    replay = _assign(client, ledger_id, "food")
    assert replay.status_code == 200
    assert replay.json()["body"]["tag_state"] == {"category": "food"}
    assert _assign(client, ledger_id, "unclassified").status_code == 200
    assert _assign(client, ledger_id, "food").status_code == 200

    detail = client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()["body"]
    assert detail["ledger_entry"]["tags"][0]["tag_system_name"] == "food"
    assert all("review_type" not in item for item in detail["reviews"])
    with sessions() as db:
        assignment = db.scalar(
            select(LedgerEntryTag).where(LedgerEntryTag.ledger_id == ledger_id)
        )
        assert db.get(TargetTag, assignment.tag_id).system_name == "food"


def test_tag_assignment_has_bounded_reads(target_tag_api):
    client, sessions, engine = target_tag_api
    fact_id = _add_facts(sessions, 1)[0]
    _create_tag_dictionary(client)
    ledger_id = _ledger_for_fact(sessions, fact_id)
    statements = []

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        response = _assign(client, ledger_id, "food")
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    assert response.status_code == 200, response.text
    assert len(statements) == 3
    assert all("SELECT *" not in statement.upper() for statement in statements)


def test_archived_tag_invalidates_assignment_and_advances_projection(target_tag_api):
    client, sessions, _engine = target_tag_api
    fact_id = _add_facts(sessions, 1)[0]
    view = _create_tag_dictionary(client)
    ledger_id = _ledger_for_fact(sessions, fact_id)
    assert _assign(client, ledger_id, "food").status_code == 200
    food = next(tag for tag in view["tags"] if tag["system_name"] == "food")

    archived = client.put(
        f"/paam/tag/v1/view/{view['id']}/tag/{food['id']}",
        json={"status": "ARCHIVED"},
    )
    assert archived.status_code == 200, archived.text
    invalidated = client.get(
        f"/paam/ledger/v1/flow/{ledger_id}"
    ).json()["body"]["ledger_entry"]
    assert invalidated["tags"][0]["tag_system_name"] == "unclassified"
    assert _assign(
        client, ledger_id, "food"
    ).status_code == 422

    restored = client.put(
        f"/paam/tag/v1/view/{view['id']}/tag/{food['id']}",
        json={"status": "ACTIVE"},
    )
    assert restored.status_code == 200, restored.text
    after_restore = client.get(
        f"/paam/ledger/v1/flow/{ledger_id}"
    ).json()["body"]["ledger_entry"]
    assert after_restore["tags"][0]["tag_system_name"] == "unclassified"
