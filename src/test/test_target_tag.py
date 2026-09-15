from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from backend.router.target_tag import router as target_tag_router
from backend.router.target_review import router as target_review_router, v2_router
from backend.router.target_economic import router as target_economic_router
from backend.router.target_dep import get_target_db
from backend.core.target_database import init_target_db
from backend.entity import (
    BillFact,
    LedgerEntryTag,
    ReviewCase,
    ReviewCaseBill,
    ReviewHistory,
    TargetTag,
    TargetTagView,
)
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
    api.include_router(target_tag_router)
    api.include_router(target_review_router)
    api.include_router(v2_router)
    api.include_router(target_economic_router)

    def override_db():
        with sessions() as db:
            yield db

    api.dependency_overrides[get_target_db] = override_db
    with TestClient(api) as client:
        yield client, sessions, engine
    engine.dispose()


def _add_facts(sessions, count, directions=None):
    now = datetime(2026, 9, 12, 12)
    with sessions() as db:
        facts = []
        for index in range(count):
            fact = BillFact(
                fact_key=uuid4().hex,
                occurred_time=now + timedelta(minutes=index),
                cash_direction=(directions[index] if directions else "OUT"),
                amount_value=1000,
                amount_scale=2,
                currency_code="CNY",
                account_code="wallet",
                counterparty="merchant",
                summary="expense",
                created_time=now,
                updated_time=now,
            )
            db.add(fact)
            facts.append(fact)
        db.flush()
        fact_ids = [fact.id for fact in facts]
        TargetEconomicService(db).ensure_defaults(fact_ids, commit=True)
        return fact_ids


def test_target_tag_dictionary_assigns_one_default_per_active_view(target_tag_api):
    client, sessions, _engine = target_tag_api
    _add_facts(sessions, 2)
    created = client.post("/paam/tag/v1/view/create", json={
        "name": "消费类别",
        "system_name": "category",
    })
    assert created.status_code == 200, created.text
    view = created.json()["body"]
    assert [(tag["name"], tag["system_name"]) for tag in view["tags"]] == [
        ("未分类", "unclassified")
    ]
    with sessions() as db:
        assert db.query(LedgerEntryTag).count() == 2

    _add_facts(sessions, 1)
    with sessions() as db:
        assert db.query(LedgerEntryTag).count() == 3

    tagged = client.post(f"/paam/tag/v1/tag/create/{view['id']}", json={
        "name": "餐饮",
        "system_name": "food",
    })
    assert tagged.status_code == 200
    food = next(item for item in tagged.json()["body"]["tags"] if item["system_name"] == "food")
    protected = client.put(
        f"/paam/tag/v1/tag/status/{view['id']}/{view['tags'][0]['id']}",
        json={"status": "ARCHIVED"},
    )
    assert protected.status_code == 422
    assert client.put(
        f"/paam/tag/v1/tag/status/{view['id']}/{food['id']}",
        json={"status": "ARCHIVED"},
    ).status_code == 200

    assert client.put(
        f"/paam/tag/v1/view/status/{view['id']}",
        json={"status": "ARCHIVED"},
    ).status_code == 200
    assert client.get("/paam/tag/v1/view/list").json()["body"] == []
    assert client.put(
        f"/paam/tag/v1/view/status/{view['id']}",
        json={"status": "ACTIVE"},
    ).status_code == 200
    with sessions() as db:
        assert db.query(LedgerEntryTag).count() == 3
        assert db.query(TargetTagView).count() == 1
        assert db.query(TargetTag).count() == 2


def test_target_tag_list_query_count_is_fixed(target_tag_api):
    client, _sessions, engine = target_tag_api
    for index in range(20):
        response = client.post("/paam/tag/v1/view/create", json={
            "name": f"View {index}",
            "system_name": f"view_{index}",
        })
        assert response.status_code == 200, response.text
    statements = []

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        response = client.get("/paam/tag/v1/view/list")
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    assert response.status_code == 200
    assert len(response.json()["body"]) == 20
    assert len(statements) == 2
    assert all("SELECT *" not in statement.upper() for statement in statements)


def _create_tag_dictionary(client):
    view = client.post("/paam/tag/v1/view/create", json={
        "name": "Category",
        "system_name": "category",
    }).json()["body"]
    response = client.post(f"/paam/tag/v1/tag/create/{view['id']}", json={
        "name": "Food",
        "system_name": "food",
    })
    assert response.status_code == 200, response.text
    return view


def _ledger_for_fact(sessions, fact_id):
    with sessions() as db:
        return db.scalar(select(ReviewCaseBill.economic_id).join(
            ReviewCase, ReviewCase.id == ReviewCaseBill.case_id,
        ).where(
            ReviewCaseBill.bill_id == fact_id,
            ReviewCaseBill.economic_id > 0,
            ReviewCase.status == "CONFIRMED",
        ))


def _assign(client, ledger_id, version, value, key):
    return client.put(f"/paam/tag/v1/assignment/set/{ledger_id}", json={
        "tag_state": {"category": value},
        "expected_version": version,
        "reason": "test tag assignment",
        "idempotency_key": key,
    })


def test_ledger_tag_assignment_is_versioned_idempotent_and_audited_per_fact(
    target_tag_api,
):
    client, sessions, _engine = target_tag_api
    fact_id = _add_facts(sessions, 1)[0]
    _create_tag_dictionary(client)
    ledger_id = _ledger_for_fact(sessions, fact_id)

    assigned = _assign(client, ledger_id, 0, "food", "assign-food")
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["body"]["version"] == 1
    assert assigned.json()["body"]["tag_state"] == {"category": "food"}

    replay = _assign(client, ledger_id, 0, "food", "assign-food")
    assert replay.status_code == 200, replay.text
    assert replay.json()["body"]["version"] == 1
    assert _assign(client, ledger_id, 0, "unclassified", "assign-food").status_code == 409
    assert _assign(client, ledger_id, 0, "food", "stale-assignment").status_code == 409
    detail = client.get(f"/paam/ledger/v2/entry/detail/{ledger_id}").json()
    assert detail["entry"]["tags"][0]["tag_system_name"] == "food"
    assert {item["review_type"] for item in detail["reviews"]} == {"DEFAULT", "TAG"}

    with sessions() as db:
        tag_case = db.scalar(select(ReviewCase).where(ReviewCase.review_type == "TAG"))
        assert tag_case.status == "CONFIRMED"
        assert tag_case.result_json == '{"tag_state":{"category":"food"}}'
        assert db.query(ReviewHistory).filter_by(case_id=tag_case.id).count() == 1
        assignment = db.scalar(select(LedgerEntryTag).where(
            LedgerEntryTag.ledger_id == ledger_id
        ))
        assert db.get(TargetTag, assignment.tag_id).system_name == "food"

    updated = _assign(client, ledger_id, 1, "unclassified", "assign-default")
    assert updated.status_code == 200, updated.text
    assert updated.json()["body"]["version"] == 2
    with sessions() as db:
        tag_case = db.scalar(select(ReviewCase).where(ReviewCase.review_type == "TAG"))
        histories = db.scalars(select(ReviewHistory).where(
            ReviewHistory.case_id == tag_case.id
        ).order_by(ReviewHistory.version)).all()
        assert tag_case.version == 2
        assert len(histories) == 2
        assert '"category":"food"' in histories[1].before_json
        assert '"category":"unclassified"' in histories[1].after_json


def test_tag_assignment_select_count_is_independent_of_split_count(target_tag_api):
    client, sessions, engine = target_tag_api
    _create_tag_dictionary(client)

    def assign_and_count(ledger_id, version, key):
        statements = []

        def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", count_selects)
        try:
            response = _assign(client, ledger_id, version, "food", key)
        finally:
            event.remove(engine, "before_cursor_execute", count_selects)
        assert response.status_code == 200, response.text
        assert all("SELECT *" not in statement.upper() for statement in statements)
        return len(statements)

    one_fact = _add_facts(sessions, 1)[0]
    one_count = assign_and_count(_ledger_for_fact(sessions, one_fact), 0, "one-tag")

    split_fact = _add_facts(sessions, 1)[0]
    created = client.post("/paam/review/v2/case/create", json={
        "behavior_code": "TWENTY_WAY_SPLIT",
        "entries": [
            {"client_key": f"part-{index}", "entry_type": 0}
            for index in range(20)
        ],
        "allocations": [
            {
                "transaction_fact_id": split_fact,
                "entry_key": f"part-{index}",
                "amount_value": 50,
            }
            for index in range(20)
        ],
        "idempotency_key": "many-create",
    }).json()["body"]
    confirmed = client.post(f"/paam/review/v2/case/confirm/{created['id']}", json={
        "expected_version": 1,
        "idempotency_key": "many-confirm",
    })
    assert confirmed.status_code == 200, confirmed.text
    many_count = assign_and_count(_ledger_for_fact(sessions, split_fact), 0, "many-tag")
    assert many_count == one_count
