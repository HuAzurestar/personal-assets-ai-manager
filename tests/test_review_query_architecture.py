import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.database import (
    Base,
    Bill,
    CandidateActionLog,
    LedgerOrigin,
    ReviewCandidate,
    ReviewMatter,
    ReviewMatterRevision,
    TagView,
    ViewTag,
)
from app.schemas import CandidateBatchDecision
from app.schemas.matter import MatterWrite
from app.schemas.review import ReviewPageQuery
from app.services.matter_service import MatterService
from app.services.candidate_suggestion_service import CandidateSuggestionService
from app.services.review_service import ReviewService


def _database(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'review-query-count.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False)


def _add_transfer_candidates(db, count: int) -> None:
    base_time = datetime(2026, 9, 1, 10, 0)
    for number in range(count):
        outbound = Bill(
            occurred_at=base_time + timedelta(minutes=number * 2),
            merchant=f"转账-{number}",
            note="",
            amount=-(number + 1),
            currency="CNY",
            category="转账",
            tags="",
            account_name="账户-A",
            aggregate_excluded=False,
            tag_state_json="{}",
        )
        inbound = Bill(
            occurred_at=base_time + timedelta(minutes=number * 2 + 1),
            merchant=f"转账-{number}",
            note="",
            amount=number + 1,
            currency="CNY",
            category="转账",
            tags="",
            account_name="账户-B",
            aggregate_excluded=False,
            tag_state_json="{}",
        )
        db.add_all([outbound, inbound])
        db.flush()
        db.add_all([
            LedgerOrigin(
                bill_id=outbound.id,
                source_type="alipay",
                source_reference=f"out-{number}",
                raw_payload="{}",
            ),
            LedgerOrigin(
                bill_id=inbound.id,
                source_type="wechat",
                source_reference=f"in-{number}",
                raw_payload="{}",
            ),
        ])
        candidate = ReviewCandidate(
            candidate_type="transfer",
            bill_id=outbound.id,
            related_bill_id=inbound.id,
            member_bill_ids=json.dumps([outbound.id, inbound.id]),
            confidence=0.8,
            reason="测试转账候选",
            status="pending",
            created_at=base_time + timedelta(minutes=number),
        )
        db.add(candidate)
        db.flush()
        db.add(CandidateActionLog(
            candidate_id=candidate.id,
            action="ignored",
            before_state="{}",
            after_state="{}",
            created_at=base_time,
        ))


def test_review_page_select_count_does_not_grow_with_rows(tmp_path):
    engine, sessions = _database(tmp_path)
    with sessions() as db:
        view = TagView(name="消费类别", system_name="category", created_at=datetime.now())
        db.add(view)
        db.flush()
        db.add(ViewTag(
            view_id=view.id,
            name="未分类",
            system_name="unclassified",
            is_unclassified=True,
        ))
        _add_transfer_candidates(db, 100)
        duplicate_bills = [Bill(
            occurred_at=datetime(2026, 9, 10, 10, 0) + timedelta(seconds=offset),
            merchant="额外重复组",
            note="",
            amount=-10,
            currency="CNY",
            category="未分类",
            tags="",
            account_name="测试账户",
            aggregate_excluded=False,
            tag_state_json="{}",
        ) for offset in (0, 30)]
        db.add_all(duplicate_bills)
        db.flush()
        db.add(ReviewCandidate(
            candidate_type="duplicate",
            bill_id=duplicate_bills[0].id,
            related_bill_id=duplicate_bills[1].id,
            member_bill_ids=json.dumps([bill.id for bill in duplicate_bills]),
            confidence=0.92,
            reason="确保归并查询也计入分页预算",
            status="pending",
            created_at=datetime(2026, 9, 10, 10, 0),
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
            small_page = ReviewService(db).page(ReviewPageQuery(page_size=10, candidate_type="transfer"))
        small_page_selects = select_count
        select_count = 0
        with sessions() as db:
            full_page = ReviewService(db).page(ReviewPageQuery(page_size=100, candidate_type="transfer"))
        full_page_selects = select_count
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
        engine.dispose()

    assert len(small_page.items) == 10
    assert len(full_page.items) == 100
    assert full_page.total == 100
    assert all(item.current_action_id for item in full_page.items)
    assert all(item.undo_available for item in full_page.items)
    assert all(len(item.member_bills) == 2 for item in full_page.items)
    assert small_page_selects == full_page_selects
    assert full_page_selects <= 11


def test_duplicate_consolidation_uses_three_set_queries(tmp_path):
    engine, sessions = _database(tmp_path)
    base_time = datetime(2026, 9, 1, 10, 0)
    with sessions() as db:
        for number in range(50):
            bills = [Bill(
                occurred_at=base_time + timedelta(hours=number, seconds=offset),
                merchant=f"重复-{number}",
                note="",
                amount=-10,
                currency="CNY",
                category="未分类",
                tags="",
                account_name="测试账户",
                aggregate_excluded=False,
                tag_state_json="{}",
            ) for offset in (0, 30)]
            db.add_all(bills)
            db.flush()
            db.add(ReviewCandidate(
                candidate_type="duplicate",
                bill_id=bills[0].id,
                related_bill_id=bills[1].id,
                member_bill_ids=json.dumps([bill.id for bill in bills]),
                confidence=0.92,
                reason="测试重复候选",
                status="pending",
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
            assert ReviewService(db).consolidate_duplicates()
            db.commit()
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
        engine.dispose()

    assert select_count == 3


def test_review_batch_select_count_does_not_grow_with_items(tmp_path):
    engine, sessions = _database(tmp_path)
    with sessions() as db:
        view = TagView(name="消费类别", system_name="category", created_at=datetime.now())
        db.add(view)
        db.flush()
        db.add(ViewTag(
            view_id=view.id,
            name="未分类",
            system_name="unclassified",
            is_unclassified=True,
        ))
        _add_transfer_candidates(db, 100)
        db.commit()
        candidate_ids = list(db.scalars(select(ReviewCandidate.id).order_by(ReviewCandidate.id)))

    select_count = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        with sessions() as db:
            one = ReviewService(db).decide_batch(CandidateBatchDecision(
                items=[{"candidate_id": candidate_ids[0], "action": "ignored"}],
            ))
        one_item_selects = select_count
        select_count = 0
        with sessions() as db:
            many = ReviewService(db).decide_batch(CandidateBatchDecision(
                items=[
                    {"candidate_id": candidate_id, "action": "ignored"}
                    for candidate_id in candidate_ids[1:]
                ],
            ))
        many_item_selects = select_count
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
        engine.dispose()

    assert len(one) == 1
    assert len(many) == 99
    assert one_item_selects == many_item_selects
    assert many_item_selects <= 13


def _seed_matters(tmp_path, count: int, suffix: str = "list"):
    engine = create_engine(
        f"sqlite:///{tmp_path / f'matter-{suffix}-query-count-{count}.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    base_time = datetime(2026, 9, 1, 10, 0)
    with sessions() as db:
        for number in range(count):
            bill = Bill(
                occurred_at=base_time + timedelta(minutes=number),
                merchant=f"事项流水-{number}",
                note="",
                amount=-10,
                currency="CNY",
                category="未分类",
                tags="",
                account_name="测试账户",
                aggregate_excluded=False,
                tag_state_json="{}",
            )
            db.add(bill)
            db.flush()
            matter = ReviewMatter(version=1, created_at=base_time)
            db.add(matter)
            db.flush()
            snapshot = {
                "title": f"事项-{number}",
                "scenarios": ["测试"],
                "lines": [{
                    "bill_id": bill.id,
                    "amount_cents": 1000,
                    "role": "expense",
                    "party": "",
                }],
                "own_accounts_confirmed": False,
                "balances": [],
            }
            db.add(ReviewMatterRevision(
                matter_id=matter.id,
                version=1,
                action="confirm",
                snapshot=json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                reason="fixture",
                actor="local-user",
                idempotency_key=f"matter-list-{number}",
                request_payload="{}",
                created_at=base_time,
            ))
        db.commit()
    return engine, sessions


def _matter_list_select_count(tmp_path, count: int) -> int:
    engine, sessions = _seed_matters(tmp_path, count)
    select_count = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        with sessions() as db:
            result = MatterService(db).list()
            assert len(result) == count
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
        engine.dispose()
    return select_count


def test_matter_list_select_count_does_not_grow_with_rows(tmp_path):
    ten_selects = _matter_list_select_count(tmp_path, 10)
    hundred_selects = _matter_list_select_count(tmp_path, 100)

    assert ten_selects == hundred_selects == 3


def _matter_page_select_count(tmp_path, page_size: int) -> int:
    engine, sessions = _seed_matters(tmp_path, 100, suffix=f"page-{page_size}")
    select_count = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        with sessions() as db:
            result = MatterService(db).page(page=1, page_size=page_size)
            assert len(result.items) == page_size
            assert result.total == 100
            assert "history" not in result.items[0].model_dump()
            assert "lines" not in result.items[0].model_dump()
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
        engine.dispose()
    return select_count


def test_matter_summary_page_is_bounded_and_omits_history(tmp_path):
    ten_selects = _matter_page_select_count(tmp_path, 10)
    hundred_selects = _matter_page_select_count(tmp_path, 100)

    assert ten_selects == hundred_selects == 2


def _matter_write_select_count(tmp_path, line_count: int) -> int:
    engine = create_engine(
        f"sqlite:///{tmp_path / f'matter-write-query-count-{line_count}.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    bill_ids = []
    with sessions() as db:
        for number in range(line_count):
            bill = Bill(
                occurred_at=datetime(2026, 9, 1, 10, 0) + timedelta(minutes=number),
                merchant=f"待分配-{number}",
                note="",
                amount=-1,
                currency="CNY",
                category="未分类",
                tags="",
                account_name="测试账户",
                aggregate_excluded=False,
                tag_state_json="{}",
            )
            db.add(bill)
            db.flush()
            bill_ids.append(bill.id)
        db.commit()

    select_count = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        with sessions() as db:
            result = MatterService(db).create(MatterWrite(
                title="批量校验",
                lines=[
                    {"bill_id": bill_id, "amount": 1, "role": "expense"}
                    for bill_id in bill_ids
                ],
                idempotency_key=f"matter-write-{line_count}",
            ))
            assert len(result.lines) == line_count
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
        engine.dispose()
    return select_count


def test_matter_write_select_count_does_not_grow_with_lines(tmp_path):
    one_selects = _matter_write_select_count(tmp_path, 1)
    hundred_selects = _matter_write_select_count(tmp_path, 100)

    assert one_selects == hundred_selects
    assert hundred_selects <= 7


def _candidate_generation_select_count(tmp_path, target_count: int) -> int:
    engine = create_engine(
        f"sqlite:///{tmp_path / f'candidate-generation-{target_count}.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    base_time = datetime(2026, 9, 1, 10, 0)
    target_ids = []
    with sessions() as db:
        db.add(Bill(
            occurred_at=base_time,
            merchant="批量重复",
            note="",
            amount=-1,
            currency="CNY",
            category="未分类",
            tags="",
            account_name="测试账户",
            aggregate_excluded=False,
            tag_state_json="{}",
        ))
        for number in range(target_count):
            bill = Bill(
                occurred_at=base_time + timedelta(seconds=number + 1),
                merchant="批量重复",
                note="",
                amount=-1,
                currency="CNY",
                category="未分类",
                tags="",
                account_name="测试账户",
                aggregate_excluded=False,
                tag_state_json="{}",
            )
            db.add(bill)
            db.flush()
            target_ids.append(bill.id)
        db.commit()

    select_count = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        with sessions() as db:
            created = CandidateSuggestionService(db).generate(target_ids)
            db.commit()
            assert created == 1
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    with sessions() as db:
        candidates = db.scalars(select(ReviewCandidate)).all()
        assert len(candidates) == 1
        assert len(json.loads(candidates[0].member_bill_ids)) == target_count + 1
    engine.dispose()
    return select_count


def test_candidate_generation_select_count_does_not_grow_with_target_bills(tmp_path):
    one_selects = _candidate_generation_select_count(tmp_path, 1)
    hundred_selects = _candidate_generation_select_count(tmp_path, 100)

    assert one_selects == hundred_selects == 4
