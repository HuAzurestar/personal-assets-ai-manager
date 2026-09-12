import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import (
    Base,
    Bill,
    ImportBatch,
    ImportIssueAction,
    ImportRowIssue,
    RefundAllocation,
    RefundDesignation,
    RefundNatureAudit,
    TagView,
    ViewTag,
)
from app.schemas import RefundAllocationCreate
from app.services.import_issue_service import ImportIssueService
from app.services.refund_service import RefundService


def _count_selects(engine, operation) -> int:
    count = 0

    def listener(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal count
        if statement.lstrip().upper().startswith("SELECT"):
            count += 1

    event.listen(engine, "before_cursor_execute", listener)
    try:
        operation()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return count


def _import_issue_select_count(tmp_path, issue_count: int, page_size: int | None = None) -> int:
    scope = page_size or "all"
    engine = create_engine(
        f"sqlite:///{tmp_path / f'import-issue-count-{issue_count}-{scope}.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    created_at = datetime(2026, 9, 1, 10, 0)
    with sessions() as db:
        batch = ImportBatch(
            source_type="alipay",
            filename="issues.csv",
            imported_at=created_at,
            row_count=issue_count,
            imported_count=0,
        )
        db.add(batch)
        db.flush()
        for number in range(issue_count):
            issue = ImportRowIssue(
                import_batch_id=batch.id,
                source_row_number=number + 1,
                raw_payload=json.dumps({"row": number}, ensure_ascii=False),
                error="缺少交易时间",
                resolution="",
            )
            db.add(issue)
            db.flush()
            db.add(ImportIssueAction(
                issue_id=issue.id,
                action="dismiss",
                payload=json.dumps({"reason": "fixture"}),
                actor="local-user",
                created_at=created_at,
            ))
        db.commit()

    def operation():
        with sessions() as db:
            if page_size:
                result = ImportIssueService(db).page(page=1, page_size=page_size)
                assert len(result.items) == page_size
                assert result.total == issue_count
                assert "raw_fields" not in result.items[0].model_dump()
                assert "history" not in result.items[0].model_dump()
            else:
                result = ImportIssueService(db).list()
                assert len(result) == issue_count
                assert all(len(issue.history) == 1 for issue in result)

    try:
        return _count_selects(engine, operation)
    finally:
        engine.dispose()


def test_import_issue_list_select_count_does_not_grow_with_rows(tmp_path):
    ten_selects = _import_issue_select_count(tmp_path, 10)
    hundred_selects = _import_issue_select_count(tmp_path, 100)

    assert ten_selects == hundred_selects == 2


def test_import_issue_summary_page_is_bounded_and_omits_raw_evidence(tmp_path):
    ten_selects = _import_issue_select_count(tmp_path, 100, page_size=10)
    hundred_selects = _import_issue_select_count(tmp_path, 100, page_size=100)

    assert ten_selects == hundred_selects == 2


def _refund_select_count(tmp_path, refund_count: int, page_size: int | None = None) -> int:
    scope = page_size or "all"
    engine = create_engine(
        f"sqlite:///{tmp_path / f'refund-count-{refund_count}-{scope}.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    created_at = datetime(2026, 9, 1, 10, 0)
    with sessions() as db:
        view = TagView(
            name="消费类别",
            system_name="category",
            created_at=created_at,
        )
        db.add(view)
        db.flush()
        db.add(ViewTag(
            view_id=view.id,
            name="未分类",
            system_name="unclassified",
            is_unclassified=True,
        ))
        for number in range(refund_count):
            refund = Bill(
                occurred_at=created_at + timedelta(minutes=number),
                merchant=f"退款-{number}",
                note="",
                amount=10,
                currency="CNY",
                category="未分类",
                tags="",
                account_name="测试账户",
                aggregate_excluded=False,
                tag_state_json='{"category":"unclassified"}',
            )
            expense = Bill(
                occurred_at=created_at - timedelta(days=1, minutes=number),
                merchant=f"原支出-{number}",
                note="",
                amount=-10,
                currency="CNY",
                category="未分类",
                tags="",
                account_name="测试账户",
                aggregate_excluded=False,
                tag_state_json='{"category":"unclassified"}',
            )
            db.add_all([refund, expense])
            db.flush()
            db.add(RefundDesignation(bill_id=refund.id, created_at=created_at))
            db.add(RefundAllocation(
                refund_bill_id=refund.id,
                expense_bill_id=expense.id,
                amount=5,
                status="confirmed",
                idempotency_key=f"refund-{number}",
                request_payload="{}",
                created_at=created_at,
            ))
            db.add(RefundNatureAudit(
                bill_id=refund.id,
                action="refund",
                reason="fixture",
                actor="local-user",
                before_nature="ordinary",
                after_nature="refund",
                request_payload="{}",
                created_at=created_at,
            ))
        db.commit()

    def operation():
        with sessions() as db:
            if page_size:
                result = RefundService(db).page(page=1, page_size=page_size)
                assert len(result.items) == page_size
                assert result.total == refund_count
                assert all(item.unallocated == 5 for item in result.items)
                item = result.items[0].model_dump()
                assert "allocations" not in item and "history" not in item
                assert "note" not in item["bill"] and "view_tags" not in item["bill"]
            else:
                result = RefundService(db).list()
                assert len(result) == refund_count
                assert all(item.unallocated == 5 for item in result)
                assert all(len(item.allocations) == len(item.history) == 1 for item in result)

    try:
        return _count_selects(engine, operation)
    finally:
        engine.dispose()


def test_refund_list_select_count_does_not_grow_with_rows(tmp_path):
    ten_selects = _refund_select_count(tmp_path, 10)
    hundred_selects = _refund_select_count(tmp_path, 100)

    assert ten_selects == hundred_selects
    assert hundred_selects <= 8


def test_refund_summary_page_is_bounded_and_omits_detail_data(tmp_path):
    ten_selects = _refund_select_count(tmp_path, 100, page_size=10)
    hundred_selects = _refund_select_count(tmp_path, 100, page_size=100)

    assert ten_selects == hundred_selects == 4


def _refund_command_select_count(tmp_path, existing_count: int) -> int:
    engine = create_engine(
        f"sqlite:///{tmp_path / f'refund-command-count-{existing_count}.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    created_at = datetime(2026, 9, 1, 10, 0)
    with sessions() as db:
        refund = Bill(
            occurred_at=created_at,
            merchant="批量退款",
            note="",
            amount=200,
            currency="CNY",
            category="未分类",
            tags="",
            account_name="测试账户",
            aggregate_excluded=False,
            tag_state_json="{}",
        )
        expense = Bill(
            occurred_at=created_at,
            merchant="原支出",
            note="",
            amount=-200,
            currency="CNY",
            category="未分类",
            tags="",
            account_name="测试账户",
            aggregate_excluded=False,
            tag_state_json="{}",
        )
        db.add_all([refund, expense])
        db.flush()
        db.add(RefundDesignation(bill_id=refund.id, created_at=created_at))
        db.add_all([
            RefundAllocation(
                refund_bill_id=refund.id,
                expense_bill_id=expense.id,
                amount=0.01,
                status="confirmed",
                idempotency_key=f"existing-{number}",
                request_payload="{}",
                created_at=created_at,
            )
            for number in range(existing_count)
        ])
        db.commit()
        refund_id = refund.id
        expense_id = expense.id

    def operation():
        with sessions() as db:
            result = RefundService(db).create_allocation(RefundAllocationCreate(
                refund_bill_id=refund_id,
                expense_bill_id=expense_id,
                amount=0.01,
                idempotency_key=f"new-{existing_count}",
            ))
            assert result.status == "confirmed"

    try:
        return _count_selects(engine, operation)
    finally:
        engine.dispose()


def test_refund_command_select_count_does_not_grow_with_existing_allocations(tmp_path):
    ten_selects = _refund_command_select_count(tmp_path, 10)
    hundred_selects = _refund_command_select_count(tmp_path, 100)

    assert ten_selects == hundred_selects == 5
