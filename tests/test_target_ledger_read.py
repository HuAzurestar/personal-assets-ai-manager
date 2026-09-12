from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.api.controllers.target_ledger import router as target_ledger_router
from app.api.deps import get_db
from app.models.target import (
    BillFact,
    BillRaw,
    ImportFile,
    LedgerEntry,
    LedgerEntrySource,
    LedgerEntryTag,
    ReviewCase,
    ReviewCaseBill,
    ReviewHistory,
    TargetTag,
    TargetTagView,
)
from app.schemas.target_ledger import TargetLedgerPageQuery
from app.services.target_ledger_service import TargetLedgerService


def _database(tmp_path, suffix: str):
    engine = create_engine(f"sqlite:///{tmp_path / f'target-ledger-read-{suffix}.db'}")
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False)


def _entry(entry_id: int, occurred: datetime) -> LedgerEntry:
    return LedgerEntry(
        id=entry_id,
        created_time=occurred,
        updated_time=occurred,
        ledger_type="EXPENSE" if entry_id % 2 else "INCOME",
        allocation_status="DEFAULT",
        title=f"流水-{entry_id}",
        start_time=occurred,
        end_time=occurred,
        in_amount_value=100 if entry_id % 2 == 0 else 0,
        in_amount_scale=2,
        in_currency_code="CNY",
        out_amount_value=100 if entry_id % 2 else 0,
        out_amount_scale=2,
        out_currency_code="CNY",
        in_account_code="income-account" if entry_id % 2 == 0 else "UNKNOWN",
        out_account_code="UNKNOWN" if entry_id % 2 == 0 else "expense-account",
        input_hash=str(entry_id).zfill(64),
        projection_version=1,
    )


def _selects(engine, operation):
    statements = []

    def listener(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", listener)
    try:
        result = operation()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return result, statements


def test_target_ledger_page_batches_tags_and_has_fixed_select_count(tmp_path):
    engine, sessions = _database(tmp_path, "page")
    started = datetime(2026, 9, 1, 10)
    with sessions() as db:
        view = TargetTagView(id=1, name="消费类别", system_name="category", status="ACTIVE")
        food = TargetTag(id=10, view_id=1, name="餐饮", system_name="food", status="ACTIVE")
        other = TargetTag(id=11, view_id=1, name="未分类", system_name="unclassified", status="ACTIVE")
        db.add_all([view, food, other])
        for index in range(100):
            entry_id = index + 1
            db.add(_entry(entry_id, started + timedelta(minutes=index)))
            db.add(LedgerEntryTag(
                id=(entry_id << 20) | (10 if entry_id % 2 else 11),
                ledger_id=entry_id,
                tag_id=10 if entry_id % 2 else 11,
            ))
        db.commit()

    def load(size, **filters):
        with sessions() as db:
            return TargetLedgerService(db).page(TargetLedgerPageQuery(
                page_size=size,
                **filters,
            ))

    small, small_sql = _selects(engine, lambda: load(10))
    full, full_sql = _selects(engine, lambda: load(100))
    tagged, tagged_sql = _selects(engine, lambda: load(100, tag=(("category", "food"),)))

    assert len(small.items) == 10
    assert len(full.items) == full.total == 100
    assert len(tagged.items) == tagged.total == 50
    assert all(item.tags[0].view_system_name == "category" for item in full.items)
    assert len(small_sql) == len(full_sql) == len(tagged_sql) == 3
    assert all("SELECT *" not in statement.upper() for statement in full_sql + tagged_sql)


def test_target_ledger_detail_loads_fact_raw_and_review_in_bounded_queries(tmp_path):
    engine, sessions = _database(tmp_path, "detail")
    occurred = datetime(2026, 9, 2, 12)
    with sessions() as db:
        db.add_all([
            TargetTagView(id=1, name="消费类别", system_name="category", status="ACTIVE"),
            TargetTag(id=10, view_id=1, name="餐饮", system_name="food", status="ACTIVE"),
            _entry(1, occurred),
            LedgerEntryTag(id=(1 << 20) | 10, ledger_id=1, tag_id=10),
            ImportFile(
                id=1,
                batch_code="batch-1",
                source_type="ALIPAY",
                institution_code="ALIPAY",
                filename="alipay.csv",
                file_format="CSV",
                sha256="a" * 64,
                period_start="2026-09-02",
                period_end="2026-09-02",
                total_count=1,
                success_count=1,
                skip_count=0,
                issue_count=0,
                status="IMPORTED",
            ),
            BillFact(
                id=1,
                fact_key="fact-1",
                occurred_time=occurred,
                cash_direction="OUT",
                amount_value=100,
                amount_scale=2,
                currency_code="CNY",
                account_code="expense-account",
                counterparty="餐厅",
                summary="午餐",
            ),
            BillRaw(
                id=1,
                bill_id=1,
                import_file_id=1,
                source_row_number=8,
                source_reference="trade-1",
                raw_payload=json.dumps({"account": "余额宝"}, ensure_ascii=False),
                raw_hash="b" * 64,
                parse_status="SUCCESS",
                issue_code="",
                issue_message="",
            ),
            LedgerEntrySource(id=2, ledger_id=1, source_kind="BILL_FACT", source_id=1),
            LedgerEntrySource(id=21, ledger_id=1, source_kind="REVIEW_CASE", source_id=10),
            ReviewCase(
                id=10,
                review_type="TAG",
                status="CONFIRMED",
                allocation_status="COMPLETE",
                version=1,
                title="确认餐饮",
                result_json='{"tag_state":{"category":"food"}}',
            ),
            ReviewCase(
                id=11,
                review_type="ACCOUNT",
                status="PENDING",
                allocation_status="PARTIAL",
                version=1,
                title="待确认账户",
                result_json='{"account":"招商银行"}',
            ),
            ReviewCaseBill(
                id=100,
                case_id=10,
                bill_id=1,
                role="TAGGED_FACT",
                party="",
                amount_value=100,
                amount_scale=2,
                currency_code="CNY",
            ),
            ReviewCaseBill(
                id=101,
                case_id=11,
                bill_id=1,
                role="ACCOUNT_FACT",
                party="",
                amount_value=100,
                amount_scale=2,
                currency_code="CNY",
            ),
            ReviewHistory(
                id=1000,
                case_id=10,
                version=1,
                operation="CREATE",
                schema_version=1,
                request_json='{"tag":"food"}',
                before_json="{}",
                after_json='{"status":"CONFIRMED"}',
                snapshot_hash="c" * 64,
                reverses_history_id=0,
                actor="local-user",
                reason="fixture",
                idempotency_key="history-10-1",
                created_time=occurred,
                updated_time=occurred,
            ),
        ])
        db.commit()

    with sessions() as db:
        detail, statements = _selects(engine, lambda: TargetLedgerService(db).detail(1))

    assert detail is not None
    assert detail.entry.outgoing.amount_value == 100
    assert detail.entry.out_account_code == "expense-account"
    assert detail.facts[0].fact_key == "fact-1"
    assert detail.facts[0].account_code == "expense-account"
    assert detail.raw_evidence[0].raw_payload == {"account": "余额宝"}
    assert detail.import_files[0].filename == "alipay.csv"
    assert [(item.id, item.is_projection_source) for item in detail.reviews] == [
        (10, True),
        (11, False),
    ]
    assert detail.reviews[0].history[0].request == {"tag": "food"}
    assert len(statements) == 10
    assert all("SELECT *" not in statement.upper() for statement in statements)


def test_target_ledger_detail_missing_entry_stops_after_one_select(tmp_path):
    engine, sessions = _database(tmp_path, "missing")
    with sessions() as db:
        detail, statements = _selects(engine, lambda: TargetLedgerService(db).detail(404))
    assert detail is None
    assert len(statements) == 1


def test_target_ledger_shadow_controller_keeps_native_integer_contract(tmp_path):
    _, sessions = _database(tmp_path, "controller")
    occurred = datetime(2026, 9, 3, 8)
    with sessions() as db:
        db.add(_entry(1, occurred))
        db.commit()

    api = FastAPI()
    api.include_router(target_ledger_router)

    def override_db():
        with sessions() as db:
            yield db

    api.dependency_overrides[get_db] = override_db
    with TestClient(api) as client:
        page = client.get("/api/shadow/v1/ledger/entries")
        assert page.status_code == 200
        item = page.json()["items"][0]
        assert item["outgoing"] == {
            "amount_value": 100,
            "amount_scale": 2,
            "currency_code": "CNY",
        }
        assert item["out_account_code"] == "expense-account"
        assert client.get("/api/shadow/v1/ledger/entries/404").status_code == 404
        assert client.get("/api/shadow/v1/ledger/summary").json()["entry_count"] == 1
        status = client.get("/api/shadow/v1/ledger/status")
        assert status.status_code == 200
        assert status.json()["ready"] is False
        assert client.get(
            "/api/shadow/v1/ledger/entries?tag=category"
        ).status_code == 422
        assert client.get(
            "/api/shadow/v1/ledger/entries?tag=category:food&tag=category:travel"
        ).status_code == 400
