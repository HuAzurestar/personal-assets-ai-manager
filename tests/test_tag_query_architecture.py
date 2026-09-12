import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, Bill, TagAudit, TagView, ViewTag
from app.schemas import TagStateBulkAssignmentRequest
from app.services.tag_service import TagService
from app.services.tag_view_service import TagViewService


def _bulk_select_count(tmp_path, size: int) -> int:
    engine = create_engine(
        f"sqlite:///{tmp_path / f'tag-query-count-{size}.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    bill_ids = []
    with sessions() as db:
        category = TagView(
            name="消费类别",
            system_name="category",
            created_at=datetime.now(),
        )
        scenario = TagView(
            name="消费场景",
            system_name="scenario",
            created_at=datetime.now(),
        )
        db.add_all([category, scenario])
        db.flush()
        db.add_all([
            ViewTag(
                view_id=category.id,
                name="未分类",
                system_name="unclassified",
                is_unclassified=True,
            ),
            ViewTag(view_id=category.id, name="餐饮", system_name="food"),
            ViewTag(
                view_id=scenario.id,
                name="未分类",
                system_name="unclassified",
                is_unclassified=True,
            ),
            ViewTag(view_id=scenario.id, name="计划内", system_name="planned"),
        ])
        base_time = datetime(2026, 9, 1, 10, 0)
        for number in range(size):
            bill = Bill(
                occurred_at=base_time + timedelta(minutes=number),
                merchant=f"批量标签-{number}",
                note="",
                amount=-(number + 1),
                currency="CNY",
                category="未分类",
                tags="",
                account_name="测试账户",
                aggregate_excluded=False,
                tag_state_json=json.dumps({
                    "category": "unclassified",
                    "scenario": "unclassified",
                }),
            )
            db.add(bill)
            db.flush()
            bill_ids.append(bill.id)
            db.add(TagAudit(
                bill_id=bill.id,
                category="未分类",
                tags="未分类,未分类",
                tag_state_json=bill.tag_state_json,
                strategy="manual",
                confidence=0.95,
                provider="fixture",
                superseded=False,
                action="confirm",
                actor="local-user",
                reason="",
                before_state_json=bill.tag_state_json,
                before_category="未分类",
                request_payload="",
                created_at=base_time,
            ))
        db.commit()

    select_count = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        with sessions() as db:
            result = TagService(db).assign_bulk(TagStateBulkAssignmentRequest(
                bill_ids=bill_ids,
                tag_state={"category": "food", "scenario": "planned"},
                idempotency_key=f"bulk-query-count-{size}",
            ))
            assert result.updated == size
        command_select_count = select_count
        event.remove(engine, "before_cursor_execute", count_selects)
        with sessions() as db:
            assert all(
                state == '{"category":"food","scenario":"planned"}'
                for state in db.scalars(
                    Bill.__table__.select()
                    .with_only_columns(Bill.tag_state_json)
                    .where(Bill.id.in_(bill_ids))
                ).all()
            )
    finally:
        if event.contains(engine, "before_cursor_execute", count_selects):
            event.remove(engine, "before_cursor_execute", count_selects)
        engine.dispose()
    return command_select_count


def test_bulk_tag_select_count_does_not_grow_with_bill_count(tmp_path):
    one_selects = _bulk_select_count(tmp_path, 1)
    hundred_selects = _bulk_select_count(tmp_path, 100)

    assert one_selects == hundred_selects
    assert hundred_selects <= 5


def _tag_view_list_select_count(tmp_path, view_count: int) -> int:
    engine = create_engine(
        f"sqlite:///{tmp_path / f'tag-view-query-count-{view_count}.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    with sessions() as db:
        for number in range(view_count):
            view = TagView(
                name=f"标签视图-{number}",
                system_name=f"dimension_{number}",
                archived=number % 2 == 0,
                created_at=datetime.now(),
            )
            db.add(view)
            db.flush()
            db.add_all([
                ViewTag(
                    view_id=view.id,
                    name="未分类",
                    system_name="unclassified",
                    is_unclassified=True,
                ),
                ViewTag(
                    view_id=view.id,
                    name=f"值-{number}",
                    system_name=f"value_{number}",
                ),
            ])
        db.commit()

    select_count = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        with sessions() as db:
            result = TagViewService(db).list(include_archived=True)
            assert len(result) == view_count
            assert all(len(view.tags) == 2 for view in result)
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
        engine.dispose()
    return select_count


def test_tag_view_list_select_count_does_not_grow_with_views(tmp_path):
    ten_selects = _tag_view_list_select_count(tmp_path, 10)
    hundred_selects = _tag_view_list_select_count(tmp_path, 100)

    assert ten_selects == hundred_selects == 2
