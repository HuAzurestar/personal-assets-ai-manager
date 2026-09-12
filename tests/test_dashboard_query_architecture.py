import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, Bill, LedgerOrigin, TagView, ViewTag
from app.schemas.ledger import LedgerPageQuery
from app.services.dashboard_service import DashboardService
from app.services.drilldown_service import DrilldownService


def test_dashboard_select_count_does_not_grow_with_filtered_rows(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'dashboard-query-count.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    with sessions() as db:
        view = TagView(name="消费类别", system_name="category", created_at=datetime.now())
        db.add(view)
        db.flush()
        db.add_all([
            ViewTag(
                view_id=view.id,
                name="未分类",
                system_name="unclassified",
                is_unclassified=True,
            ),
            ViewTag(view_id=view.id, name="餐饮", system_name="food"),
        ])
        base_time = datetime(2026, 9, 1, 10, 0)
        for number in range(100):
            bill = Bill(
                occurred_at=base_time + timedelta(minutes=number),
                merchant=f"汇总-{number}",
                note="",
                amount=-(number + 1),
                currency="CNY",
                category="餐饮",
                tags="",
                account_name="small" if number < 10 else "large",
                aggregate_excluded=False,
                tag_state_json=json.dumps({"category": "food"}),
            )
            db.add(bill)
            db.flush()
            db.add(LedgerOrigin(
                bill_id=bill.id,
                source_type="alipay",
                source_reference=str(number),
                raw_payload="{}",
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
            small = DashboardService(db).summary(LedgerPageQuery(
                account=("small",),
                tag=("category:food",),
            ))
        small_selects = select_count
        select_count = 0
        with sessions() as db:
            full = DashboardService(db).summary(LedgerPageQuery(
                account=("small", "large"),
                tag=("category:food",),
            ))
        full_selects = select_count
        select_count = 0
        with sessions() as db:
            small_drilldown = DrilldownService(db).report(LedgerPageQuery(
                account=("small",),
                tag=("category:food",),
            ))
        small_drilldown_selects = select_count
        select_count = 0
        with sessions() as db:
            full_drilldown = DrilldownService(db).report(LedgerPageQuery(
                account=("small", "large"),
                tag=("category:food",),
            ))
        full_drilldown_selects = select_count
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
        engine.dispose()

    assert small["bill_count"] == 10
    assert full["bill_count"] == 100
    assert small_selects == full_selects
    assert full_selects <= 9
    assert len(small_drilldown["transactions"]) == 10
    assert len(full_drilldown["transactions"]) == 100
    assert small_drilldown_selects == full_drilldown_selects
    assert full_drilldown_selects <= 24
