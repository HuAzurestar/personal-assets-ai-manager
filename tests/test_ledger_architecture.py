import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, Bill, LedgerOrigin, TagAudit, TagView, ViewTag
from app.schemas.ledger import LedgerPageQuery
from app.services.ledger_service import LedgerService


def test_ledger_page_uses_fixed_number_of_set_queries(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ledger-query-count.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    with sessions() as db:
        category = TagView(name="消费类别", system_name="category", created_at=datetime.now())
        scenario = TagView(name="使用场景", system_name="scenario", created_at=datetime.now())
        db.add_all([category, scenario])
        db.flush()
        db.add_all([
            ViewTag(view_id=category.id, name="未分类", system_name="unclassified", is_unclassified=True),
            ViewTag(view_id=category.id, name="餐饮", system_name="food"),
            ViewTag(view_id=scenario.id, name="未分类", system_name="unclassified", is_unclassified=True),
            ViewTag(view_id=scenario.id, name="日常", system_name="daily"),
        ])
        base_time = datetime(2026, 9, 1, 10, 0)
        for number in range(100):
            bill = Bill(
                occurred_at=base_time + timedelta(minutes=number),
                merchant=f"流水-{number}",
                note="",
                amount=-(number + 1),
                category="餐饮",
                tags="",
                account_name="测试账户",
                aggregate_excluded=False,
                tag_state_json=json.dumps({"category": "food", "scenario": "daily"}),
            )
            db.add(bill)
            db.flush()
            db.add(LedgerOrigin(
                bill_id=bill.id,
                source_type="alipay",
                source_reference=str(number),
                raw_payload="{}",
                source_row_number=number + 1,
            ))
            db.add(TagAudit(
                bill_id=bill.id,
                category="餐饮",
                tags="餐饮,日常",
                tag_state_json=bill.tag_state_json,
                strategy="manual",
                confidence=1.0,
                superseded=False,
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
            small_page = LedgerService(db).page(LedgerPageQuery(page_size=10))
        small_page_selects = select_count
        select_count = 0
        with sessions() as db:
            page = LedgerService(db).page(LedgerPageQuery(page_size=100))
        full_page_selects = select_count
        select_count = 0
        with sessions() as db:
            legacy_bills = LedgerService(db).all()
        legacy_selects = select_count
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
        engine.dispose()

    assert len(page.items) == 100
    assert page.total == 100
    assert all(item.source_type == "alipay" for item in page.items)
    assert all(item.tag_revision_id for item in page.items)
    # Tag dictionary (2), count (1), page IDs (1), explicit page rows (1),
    # origins (1), and current revision IDs (1). Row count never changes it.
    assert len(small_page.items) == 10
    assert small_page_selects == full_page_selects
    assert full_page_selects <= 7
    assert len(legacy_bills) == 100
    assert legacy_selects <= 6
