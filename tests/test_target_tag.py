from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.api.controllers.target_tag import router as target_tag_router
from app.api.deps import get_db
from app.database import init_target_db
from app.models.target import BillFact, LedgerEntryTag, TargetTag, TargetTagView
from app.services.target_projection_service import TargetProjectionService


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
        facts = []
        for index in range(count):
            fact = BillFact(
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
