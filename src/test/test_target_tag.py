from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from backend.router.tag import router as tag_router
from backend.router.tag_assignment import router as tag_assignment_router
from backend.router.ledger_review_legacy import router as ledger_review_legacy_router
from backend.router.ledger_legacy import router as ledger_legacy_router
from backend.router.dependency import get_db
from backend.core.target_database import init_target_db
from backend.entity import (
    BillFact,
    LedgerEntrySource,
    LedgerEntryTag,
    ReviewCase,
    ReviewHistory,
    TargetTag,
    TargetTagView,
)
from backend.service.target_projection_service import TargetProjectionService


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
    api.include_router(ledger_review_legacy_router)
    api.include_router(ledger_legacy_router)

    def override_db():
        with sessions() as db:
            yield db

    api.dependency_overrides[get_db] = override_db
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
        TargetProjectionService(db).rebuild_defaults(fact_ids)
        db.commit()
        return fact_ids


def test_target_tag_dictionary_assigns_one_default_per_active_view(target_tag_api):
    client, sessions, _engine = target_tag_api
    _add_facts(sessions, 2)
    created = client.post("/paam/tag/v1/view", json={
        "name": "消费类别",
        "system_name": "category",
    })
    assert created.status_code == 200, created.text
    assert created.json()["status"] == created.status_code
    assert created.json()["message"] == "Tag view created"
    view = created.json()["body"]
    assert [(tag["name"], tag["system_name"]) for tag in view["tags"]] == [
        ("未分类", "unclassified")
    ]
    with sessions() as db:
        assert db.query(LedgerEntryTag).count() == 2

    _add_facts(sessions, 1)
    with sessions() as db:
        assert db.query(LedgerEntryTag).count() == 3

    tagged = client.post(f"/paam/tag/v1/view/{view['id']}/tag", json={
        "name": "餐饮",
        "system_name": "food",
    })
    assert tagged.status_code == 200
    food = next(item for item in tagged.json()["body"]["tags"] if item["system_name"] == "food")
    protected = client.put(
        f"/paam/tag/v1/view/{view['id']}/tag/{view['tags'][0]['id']}",
        json={"status": "ARCHIVED"},
    )
    assert protected.status_code == 422
    assert client.put(
        f"/paam/tag/v1/view/{view['id']}/tag/{food['id']}",
        json={"status": "ARCHIVED"},
    ).status_code == 200

    assert client.put(
        f"/paam/tag/v1/view/{view['id']}",
        json={"status": "ARCHIVED"},
    ).status_code == 200
    assert client.get("/paam/tag/v1/view/list").json()["body"]["items"] == []
    assert client.put(
        f"/paam/tag/v1/view/{view['id']}",
        json={"status": "ACTIVE"},
    ).status_code == 200
    with sessions() as db:
        assert db.query(LedgerEntryTag).count() == 3
        assert db.query(TargetTagView).count() == 1
        assert db.query(TargetTag).count() == 2


def test_target_tag_list_query_count_is_fixed(target_tag_api):
    client, _sessions, engine = target_tag_api
    for index in range(20):
        response = client.post("/paam/tag/v1/view", json={
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
    assert response.json()["status"] == response.status_code
    assert response.json()["message"] == "Tag view list retrieved"
    body = response.json()["body"]
    assert set(body) == {"items", "total", "page", "page_size"}
    assert (body["total"], body["page"], body["page_size"]) == (20, 1, 20)
    assert len(body["items"]) == 20
    assert len(statements) == 3
    assert all("SELECT *" not in statement.upper() for statement in statements)


def _create_tag_dictionary(client):
    view = client.post("/paam/tag/v1/view", json={
        "name": "Category",
        "system_name": "category",
    }).json()["body"]
    response = client.post(f"/paam/tag/v1/view/{view['id']}/tag", json={
        "name": "Food",
        "system_name": "food",
    })
    assert response.status_code == 200, response.text
    return view


def _ledger_for_fact(sessions, fact_id):
    with sessions() as db:
        return db.scalar(select(LedgerEntrySource.ledger_id).where(
            LedgerEntrySource.source_kind == "BILL_FACT",
            LedgerEntrySource.source_id == fact_id,
        ))


def _assign(client, ledger_id, version, value, key):
    return client.put(f"/paam/tag/v1/assignment/set/{ledger_id}", json={
        "tag_state": {"category": value},
        "expected_projection_version": version,
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

    assigned = _assign(client, ledger_id, 1, "food", "assign-food")
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["body"]["projection_version"] == 2
    assert assigned.json()["body"]["tag_state"] == {"category": "food"}

    replay = _assign(client, ledger_id, 1, "food", "assign-food")
    assert replay.status_code == 200, replay.text
    assert replay.json()["body"]["projection_version"] == 2
    assert _assign(client, ledger_id, 1, "unclassified", "assign-food").status_code == 409
    assert _assign(client, ledger_id, 1, "food", "stale-assignment").status_code == 409
    detail = client.get(f"/paam/ledger/v1/entry/detail/{ledger_id}").json()
    assert detail["entry"]["tags"][0]["tag_system_name"] == "food"
    assert detail["reviews"][0]["review_type"] == "TAG"
    assert detail["reviews"][0]["is_projection_source"] is True

    with sessions() as db:
        tag_case = db.scalar(select(ReviewCase).where(ReviewCase.review_type == "TAG"))
        assert tag_case.status == "CONFIRMED"
        assert tag_case.result_json == '{"tag_state":{"category":"food"}}'
        assert db.query(ReviewHistory).filter_by(case_id=tag_case.id).count() == 1
        assignment = db.scalar(select(LedgerEntryTag).where(
            LedgerEntryTag.ledger_id == ledger_id
        ))
        assert db.get(TargetTag, assignment.tag_id).system_name == "food"

    updated = _assign(client, ledger_id, 2, "unclassified", "assign-default")
    assert updated.status_code == 200, updated.text
    assert updated.json()["body"]["projection_version"] == 3
    with sessions() as db:
        tag_case = db.scalar(select(ReviewCase).where(ReviewCase.review_type == "TAG"))
        histories = db.scalars(select(ReviewHistory).where(
            ReviewHistory.case_id == tag_case.id
        ).order_by(ReviewHistory.version)).all()
        assert tag_case.version == 2
        assert len(histories) == 2
        assert '"category":"food"' in histories[1].before_json
        assert '"category":"unclassified"' in histories[1].after_json


def test_merged_ledger_tag_assignment_survives_financial_split_and_restore(
    target_tag_api,
):
    client, sessions, _engine = target_tag_api
    out_id, in_id = _add_facts(sessions, 2, ["OUT", "IN"])
    _create_tag_dictionary(client)
    created = client.post("/paam/review/v1/case/create", json={
        "review_type": "TRANSFER",
        "title": "Transfer",
        "result": {},
        "lines": [
            {"bill_id": out_id, "role": "TRANSFER_OUT"},
            {"bill_id": in_id, "role": "TRANSFER_IN"},
        ],
        "idempotency_key": "transfer-create",
    })
    assert created.status_code == 200, created.text
    case = created.json()["body"]
    confirmed = client.post(f"/paam/review/v1/case/confirm/{case['id']}", json={
        "expected_version": 1,
        "idempotency_key": "transfer-confirm",
    })
    assert confirmed.status_code == 200, confirmed.text
    ledger = client.get("/paam/ledger/v1/entry/list").json()["items"][0]
    assigned = _assign(client, ledger["id"], ledger["projection_version"], "food", "merged-food")
    assert assigned.status_code == 200, assigned.text
    assert len(assigned.json()["body"]["review_case_ids"]) == 2

    revoked = client.post(f"/paam/review/v1/case/revoke/{case['id']}", json={
        "expected_version": 2,
        "idempotency_key": "transfer-revoke",
    })
    assert revoked.status_code == 200, revoked.text
    split = client.get("/paam/ledger/v1/entry/list").json()["items"]
    assert len(split) == 2
    assert all(item["tags"][0]["tag_system_name"] == "food" for item in split)

    restored = client.post(f"/paam/review/v1/case/restore/{case['id']}", json={
        "expected_version": 3,
        "idempotency_key": "transfer-restore",
    })
    assert restored.status_code == 200, restored.text
    merged = client.get("/paam/ledger/v1/entry/list").json()["items"]
    assert len(merged) == 1
    assert merged[0]["tags"][0]["tag_system_name"] == "food"


def test_financial_merge_rejects_different_fact_tag_states(target_tag_api):
    client, sessions, _engine = target_tag_api
    out_id, in_id = _add_facts(sessions, 2, ["OUT", "IN"])
    _create_tag_dictionary(client)
    first_ledger = _ledger_for_fact(sessions, out_id)
    second_ledger = _ledger_for_fact(sessions, in_id)
    assert _assign(client, first_ledger, 1, "food", "first-food").status_code == 200
    assert _assign(client, second_ledger, 1, "unclassified", "second-default").status_code == 200
    created = client.post("/paam/review/v1/case/create", json={
        "review_type": "TRANSFER",
        "title": "Conflicting tags",
        "result": {},
        "lines": [
            {"bill_id": out_id, "role": "TRANSFER_OUT"},
            {"bill_id": in_id, "role": "TRANSFER_IN"},
        ],
        "idempotency_key": "conflict-create",
    }).json()["body"]
    confirmed = client.post(f"/paam/review/v1/case/confirm/{created['id']}", json={
        "expected_version": 1,
        "idempotency_key": "conflict-confirm",
    })
    assert confirmed.status_code == 422
    with sessions() as db:
        assert db.get(ReviewCase, created["id"]).status == "PENDING"


def test_tag_assignment_select_count_is_independent_of_source_fact_count(
    target_tag_api,
):
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
    one_count = assign_and_count(_ledger_for_fact(sessions, one_fact), 1, "one-tag")

    many_facts = _add_facts(sessions, 20)
    created = client.post("/paam/review/v1/case/create", json={
        "review_type": "AA",
        "title": "Grouped expenses",
        "result": {},
        "lines": [
            {"bill_id": fact_id, "role": "AA_PAID"}
            for fact_id in many_facts
        ],
        "idempotency_key": "many-create",
    }).json()["body"]
    confirmed = client.post(f"/paam/review/v1/case/confirm/{created['id']}", json={
        "expected_version": 1,
        "idempotency_key": "many-confirm",
    })
    assert confirmed.status_code == 200, confirmed.text
    many_ledger = _ledger_for_fact(sessions, many_facts[0])
    many_count = assign_and_count(many_ledger, 2, "many-tag")
    assert one_count == 6
    assert many_count == one_count


def test_tag_view_restore_reports_merge_conflict_and_rolls_back(target_tag_api):
    client, sessions, _engine = target_tag_api
    fact_ids = _add_facts(sessions, 2, directions=["OUT", "IN"])
    view = client.post("/paam/tag/v1/view", json={
        "name": "Category", "system_name": "category",
    }).json()["body"]
    view = client.post(f"/paam/tag/v1/view/{view['id']}/tag", json={
        "name": "Food", "system_name": "food",
    }).json()["body"]
    entries = client.get("/paam/ledger/v1/entry/list").json()["items"]
    assigned = client.put(f"/paam/tag/v1/assignment/set/{entries[0]['id']}", json={
        "tag_state": {"category": "food"},
        "expected_projection_version": entries[0]["projection_version"],
        "idempotency_key": "restore-conflict-tag",
    })
    assert assigned.status_code == 200, assigned.text
    assert client.put(f"/paam/tag/v1/view/{view['id']}", json={
        "status": "ARCHIVED",
    }).status_code == 200
    case = client.post("/paam/review/v1/case/create", json={
        "review_type": "TRANSFER",
        "lines": [
            {"bill_id": fact_ids[0], "role": "TRANSFER_OUT"},
            {"bill_id": fact_ids[1], "role": "TRANSFER_IN"},
        ],
        "idempotency_key": "restore-conflict-create",
    }).json()["body"]
    assert client.post(f"/paam/review/v1/case/confirm/{case['id']}", json={
        "expected_version": 1,
        "idempotency_key": "restore-conflict-confirm",
    }).status_code == 200
    restored = client.put(f"/paam/tag/v1/view/{view['id']}", json={
        "status": "ACTIVE",
    })
    assert restored.status_code == 409
    assert restored.json()["status"] == 409
    assert "different category tags" in restored.json()["message"]
    assert restored.json()["body"]["code"] == "TAG_ERROR"
    archived = client.get(
        "/paam/tag/v1/view/list?include_archived=true"
    ).json()["body"]["items"]
    assert archived[0]["status"] == "ARCHIVED"
