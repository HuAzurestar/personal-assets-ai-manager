from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from backend.core.target_database import init_target_db
from backend.entity import (
    BillFact,
    LedgerEntryTag,
    ReviewCase,
    ReviewCaseBill,
    TargetTag,
    TargetTagView,
)
from backend.router.dependency import get_db
from backend.router.ledger import router as ledger_router
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

    def override_db():
        with sessions() as db:
            yield db

    api.dependency_overrides[get_db] = override_db
    with TestClient(api) as client:
        yield client, sessions, engine
    engine.dispose()


def _add_facts(sessions, count):
    now = datetime(2026, 9, 12, 12)
    with sessions() as db:
        facts = [
            BillFact(
                fact_key=uuid4().hex,
                occurred_time=now + timedelta(minutes=index),
                cash_direction="OUT",
                amount_value=1000,
                amount_scale=2,
                currency_code="CNY",
                account_code="wallet",
                counterparty="merchant",
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
            select(ReviewCaseBill.economic_id)
            .join(ReviewCase, ReviewCase.id == ReviewCaseBill.case_id)
            .where(
                ReviewCaseBill.bill_id == fact_id,
                ReviewCaseBill.economic_id > 0,
                ReviewCase.status == "CONFIRMED",
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


def _assign(client, ledger_id, version, value):
    return client.put(
        f"/paam/tag/v1/assignment/{ledger_id}",
        json={
            "tag_state": {"category": value},
            "expected_projection_version": version,
        },
    )


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
    assert client.get("/paam/tag/v1/view/list").json()["body"]["items"] == []
    assert client.put(
        f"/paam/tag/v1/view/{view['id']}", json={"status": "ACTIVE"}
    ).status_code == 200
    with sessions() as db:
        assert db.query(LedgerEntryTag).count() == 3
        assert db.query(TargetTagView).count() == 1
        assert db.query(TargetTag).count() == 2


def test_target_tag_list_query_count_is_fixed(target_tag_api):
    client, _sessions, engine = target_tag_api
    for index in range(20):
        response = client.post(
            "/paam/tag/v1/view",
            json={"name": f"View {index}", "system_name": f"view_{index}"},
        )
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
    body = response.json()["body"]
    assert (body["total"], body["page"], body["page_size"]) == (20, 1, 20)
    assert len(body["items"]) == 20
    assert len(statements) == 3
    assert all("SELECT *" not in statement.upper() for statement in statements)


def test_ledger_tag_assignment_is_direct_versioned_and_idempotent(target_tag_api):
    client, sessions, _engine = target_tag_api
    fact_id = _add_facts(sessions, 1)[0]
    _create_tag_dictionary(client)
    ledger_id = _ledger_for_fact(sessions, fact_id)

    assigned = _assign(client, ledger_id, 1, "food")
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["body"] == {
        "ledger_id": ledger_id,
        "projection_version": 2,
        "tag_state": {"category": "food"},
    }
    replay = _assign(client, ledger_id, 1, "food")
    assert replay.status_code == 200
    assert replay.json()["body"]["projection_version"] == 2
    assert _assign(client, ledger_id, 1, "unclassified").status_code == 409

    detail = client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()
    assert detail["entry"]["projection_version"] == 2
    assert detail["entry"]["tags"][0]["tag_system_name"] == "food"
    assert all(item["review_type"] != "TAG" for item in detail["reviews"])
    with sessions() as db:
        assert db.scalar(select(ReviewCase).where(ReviewCase.review_type == "TAG")) is None
        assignment = db.scalar(
            select(LedgerEntryTag).where(LedgerEntryTag.ledger_id == ledger_id)
        )
        assert db.get(TargetTag, assignment.tag_id).system_name == "food"


def test_tag_assignment_has_bounded_reads_and_no_review_dependency(target_tag_api):
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
        response = _assign(client, ledger_id, 1, "food")
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    assert response.status_code == 200, response.text
    assert len(statements) == 3
    assert all("review_case" not in statement.lower() for statement in statements)
    assert all("SELECT *" not in statement.upper() for statement in statements)
