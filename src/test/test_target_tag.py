from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from backend.core.target_database import init_target_db
from backend.error import TargetTagError
from backend.entity import (
    CASH_DIRECTION_OUT,
    LedgerEntry,
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
from backend.schema.target_review import TargetReviewTransitionRequest
from backend.schema.target_tag import TargetTagViewCreateRequest
from backend.service.target_economic_service import TargetEconomicService
from backend.service.target_tag_service import TargetTagService


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


_LATEST_ASSIGNMENT = object()


def _assignment(client, ledger_id):
    response = client.get(f"/paam/tag/v1/assignment/{ledger_id}")
    assert response.status_code == 200, response.text
    return response.json()["body"]


def _assign(client, ledger_id, value, expected_updated_time=_LATEST_ASSIGNMENT):
    if expected_updated_time is _LATEST_ASSIGNMENT:
        expected_updated_time = _assignment(client, ledger_id)["updated_time"]
    return client.put(
        f"/paam/tag/v1/assignment/{ledger_id}",
        json={
            "expected_updated_time": expected_updated_time,
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


def test_target_tag_view_count_is_limited_to_100(target_tag_api):
    client, sessions, _engine = target_tag_api
    now = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
    with sessions() as db:
        db.add_all([
            TargetTagView(
                name=f"View {index}",
                system_name=f"view_{index}",
                status="ACTIVE",
                created_time=now,
                updated_time=now,
            )
            for index in range(100)
        ])
        db.commit()

    rejected = client.post(
        "/paam/tag/v1/view",
        json={"name": "Overflow", "system_name": "overflow"},
    )
    assert rejected.status_code == 422
    assert rejected.json()["message"] == "tag view limit is 100"


def test_target_tag_view_limit_is_atomic_under_concurrent_creates(target_tag_api):
    _client, sessions, _engine = target_tag_api
    now = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
    with sessions() as db:
        db.add_all([
            TargetTagView(
                name=f"View {index}",
                system_name=f"view_{index}",
                status="ACTIVE",
                created_time=now,
                updated_time=now,
            )
            for index in range(99)
        ])
        db.commit()

    barrier = Barrier(2)

    def create(index: int) -> int:
        with sessions() as db:
            barrier.wait()
            try:
                TargetTagService(db).create_view(TargetTagViewCreateRequest(
                    name=f"Concurrent {index}",
                    system_name=f"concurrent_{index}",
                ))
                return 200
            except TargetTagError as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(create, range(2)))

    assert statuses == [200, 422]
    with sessions() as db:
        assert db.query(TargetTagView).count() == 100


def test_ledger_tag_assignment_is_direct_and_idempotent(target_tag_api):
    client, sessions, _engine = target_tag_api
    fact_id = _add_facts(sessions, 1)[0]
    _create_tag_dictionary(client)
    ledger_id = _ledger_for_fact(sessions, fact_id)
    with sessions() as db:
        previous_updated_time = db.get(LedgerEntry, ledger_id).updated_time
    opened = _assignment(client, ledger_id)

    assigned = _assign(client, ledger_id, "food", opened["updated_time"])
    assert assigned.status_code == 200, assigned.text
    assigned_body = assigned.json()["body"]
    assert assigned_body["ledger_id"] == ledger_id
    assert assigned_body["tag_state"] == {"category": "food"}
    assert assigned_body["updated_time"] > opened["updated_time"]
    immediate_replay = _assign(
        client,
        ledger_id,
        "food",
        assigned_body["updated_time"],
    )
    assert immediate_replay.status_code == 200, immediate_replay.text
    assert (
        immediate_replay.json()["body"]["updated_time"]
        == assigned_body["updated_time"]
    )
    replay = _assign(client, ledger_id, "food")
    assert replay.status_code == 200
    assert replay.json()["body"]["tag_state"] == {"category": "food"}
    assert _assign(client, ledger_id, "unclassified").status_code == 200
    assert _assign(client, ledger_id, "food").status_code == 200

    detail = client.get(f"/paam/ledger/v1/flow/{ledger_id}").json()["body"]
    assert detail["tags"][0]["tag_system_name"] == "food"
    assert all("review_type" not in item for item in detail["reviews"])
    with sessions() as db:
        assert db.get(LedgerEntry, ledger_id).updated_time == previous_updated_time
        assignment = db.scalar(
            select(LedgerEntryTag).where(LedgerEntryTag.ledger_id == ledger_id)
        )
        assert db.get(TargetTag, assignment.tag_id).system_name == "food"


def test_stale_tag_assignment_is_rejected(target_tag_api):
    client, sessions, _engine = target_tag_api
    fact_id = _add_facts(sessions, 1)[0]
    _create_tag_dictionary(client)
    ledger_id = _ledger_for_fact(sessions, fact_id)
    opened = _assignment(client, ledger_id)

    current = _assign(client, ledger_id, "food", opened["updated_time"])
    assert current.status_code == 200, current.text
    stale = _assign(client, ledger_id, "unclassified", opened["updated_time"])
    assert stale.status_code == 409
    assert stale.json()["message"] == "tag assignment changed; reload before writing"
    assert _assignment(client, ledger_id)["tag_state"] == {"category": "food"}


def test_archived_view_is_excluded_from_assignment_state(target_tag_api):
    client, sessions, _engine = target_tag_api
    fact_id = _add_facts(sessions, 1)[0]
    view = _create_tag_dictionary(client)
    ledger_id = _ledger_for_fact(sessions, fact_id)

    archived = client.put(
        f"/paam/tag/v1/view/{view['id']}",
        json={"status": "ARCHIVED"},
    )
    assert archived.status_code == 200, archived.text
    active_views = client.get("/paam/tag/v1/view/list", params={
        "page_size": 100,
        "filter": '{"key":"status","op":"=","val":"ACTIVE"}',
    }).json()["body"]
    assert active_views["items"] == []
    assert _assignment(client, ledger_id)["tag_state"] == {}


def test_inactive_ledger_tag_assignment_is_read_only(target_tag_api):
    client, sessions, _engine = target_tag_api
    fact_id = _add_facts(sessions, 1)[0]
    _create_tag_dictionary(client)
    ledger_id = _ledger_for_fact(sessions, fact_id)
    with sessions() as db:
        case_id = db.scalar(select(ReviewAllocation.review_case_id).where(
            ReviewAllocation.ledger_entry_id == ledger_id,
        ))
        from backend.schema.review_command import ReviewChangeInput, ReviewCommandInput
        from backend.service.review_command_service import ReviewCommandService
        service = ReviewCommandService(db)
        intent = dict(new_reviews=[dict(case_code="NORMAL", parameters=dict(transaction_ids=[fact_id]))])
        preview = service.preview(ReviewChangeInput(**intent))
        service.command(ReviewCommandInput(**intent, expected_reviews=preview["expected_reviews"], preview_digest=preview["preview_digest"]))

    assigned = _assign(client, ledger_id, "food")
    assert assigned.status_code == 409, assigned.text
    assert assigned.json()["body"]["code"] == "LEDGER_INACTIVE"
    with sessions() as db:
        assignment = db.scalar(select(LedgerEntryTag).where(
            LedgerEntryTag.ledger_id == ledger_id,
        ))
        assert db.get(TargetTag, assignment.tag_id).system_name == "unclassified"
    assert _assignment(client, ledger_id)["tag_state"] == {"category": "unclassified"}


def test_tag_assignment_has_bounded_reads(target_tag_api):
    client, sessions, engine = target_tag_api
    fact_id = _add_facts(sessions, 1)[0]
    _create_tag_dictionary(client)
    ledger_id = _ledger_for_fact(sessions, fact_id)
    expected_updated_time = _assignment(client, ledger_id)["updated_time"]
    statements = []

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        response = _assign(client, ledger_id, "food", expected_updated_time)
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
    ).json()["body"]
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
    ).json()["body"]
    assert after_restore["tags"][0]["tag_system_name"] == "unclassified"


def test_tag_batch_encodes_each_ledger_timestamp_once_without_losing_precision(monkeypatch):
    from sqlalchemy import update
    from backend.core import target_database
    from backend.entity.base import UTCISO8601DateTime
    from backend.mapper.target_tag_mapper import TargetTagMapper
    from backend.mapper import target_tag_projection_mapper as projection

    bindings = []
    original = UTCISO8601DateTime.process_bind_param

    def observed(self, value, dialect):
        bindings.append(value)
        return original(self, value, dialect)

    # Install before this isolated engine first binds timestamps, not after its
    # dialect has cached a processor for fixture setup.
    monkeypatch.setattr(UTCISO8601DateTime, "process_bind_param", observed)
    target_database.ensure_target_schema()
    sessions = target_database.SessionLocal
    now = datetime(2030, 1, 1, microsecond=7, tzinfo=timezone.utc)
    future = now + timedelta(days=1, microseconds=10)
    with sessions() as db:
        mapper = TargetTagMapper(db)
        mapper.begin_write()
        for index in range(4):
            view_id = mapper.create_view(f"Mock clock {index}", f"mock_clock_{index}", now)
            mapper.create_tag(view_id, "Mock choice", "choice", now)
        mapper.commit()
    ledger_ids = [_ledger_for_fact(sessions, fact_id) for fact_id in _add_facts(sessions, 2)]
    with sessions() as db:
        db.execute(update(LedgerEntryTag).where(LedgerEntryTag.ledger_id == ledger_ids[1])
            .values(updated_time=future))
        db.commit()
    monkeypatch.setattr(projection, "utc_now", lambda: now)
    with sessions() as db:
        tag_ids = tuple(db.scalars(select(TargetTag.id).where(TargetTag.system_name == "choice")
            .order_by(TargetTag.id)))
        assert len(tag_ids) == 4
        entities = (TransactionFact, ReviewCase, LedgerEntry, ReviewAllocation)
        def financial():
            return [tuple(tuple(row) for row in db.execute(select(entity.__table__).order_by(entity.id)))
                for entity in entities]
        before = financial()
        bindings.clear()
        mapper = projection.TargetTagProjectionMapper(db)
        mapper.replace({ledger_id: tag_ids for ledger_id in ledger_ids})
        assert bindings == [now, now, future + timedelta(microseconds=1), future + timedelta(microseconds=1)]
        rows = db.connection().exec_driver_sql(
            "SELECT ledger_id, tag_id, created_time, updated_time FROM ledger_entry_tag ORDER BY ledger_id, tag_id"
        ).all()
        assert len(rows) == 8
        for ledger_id, tag_id, created, updated in rows:
            expected = now if ledger_id == ledger_ids[0] else future + timedelta(microseconds=1)
            assert tag_id in tag_ids
            assert created == updated == expected.isoformat(timespec="microseconds").replace("+00:00", "Z")
        mapper.replace({ledger_id: tag_ids for ledger_id in ledger_ids})
        assert len(bindings) == 4, "an unchanged complete tag state binds no replacement timestamps"
        assert financial() == before
        db.commit()
