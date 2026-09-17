from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.core.target_database import init_target_db
from backend.entity import (
    CASH_DIRECTION_OUT,
    IMPORT_FILE_FORMAT_CSV,
    IMPORT_FILE_STATUS_IMPORTED,
    IMPORT_ROW_STATUS_ACCEPTED,
    IMPORT_ROW_STATUS_SKIPPED,
    IMPORT_SOURCE_ALIPAY,
    IMPORT_SOURCE_WECHAT,
    TransactionFact,
    TransactionImportFile,
    TransactionImportRow,
)
from backend.router.dependency import get_db
from backend.router.import_file import router


@pytest.fixture
def import_file_api(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'import-file-api.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False)
    init_target_db(bind=engine)
    api = FastAPI()
    api.include_router(router)

    def override_db():
        with sessions() as db:
            yield db

    api.dependency_overrides[get_db] = override_db
    with TestClient(api) as client:
        yield client, sessions
    engine.dispose()


def _seed(sessions):
    now = datetime(2026, 9, 16, 8)
    with sessions() as db:
        files = [
            TransactionImportFile(
                batch_code=f"batch-{index}",
                source_type=source,
                filename=filename,
                file_format=IMPORT_FILE_FORMAT_CSV,
                sha256=str(index) * 64,
                period_start="2026-09-01",
                period_end="2026-09-30",
                total_count=1,
                success_count=1,
                skip_count=0,
                issue_count=0,
                status=IMPORT_FILE_STATUS_IMPORTED,
                created_time=now + timedelta(minutes=index),
                updated_time=now + timedelta(minutes=index),
            )
            for index, source, filename in (
                (1, IMPORT_SOURCE_WECHAT, "wechat-september.csv"),
                (2, IMPORT_SOURCE_ALIPAY, "alipay-september.csv"),
            )
        ]
        fact = TransactionFact(
            fact_key="shared-import-fact",
            occurred_time=now,
            cash_direction=CASH_DIRECTION_OUT,
            amount=880,
            currency_code="CNY",
            account_code="wallet",
            counterparty_name="Merchant",
            counterparty_account_ref="",
            summary="Lunch",
            created_time=now,
            updated_time=now,
        )
        db.add_all([*files, fact])
        db.flush()
        db.add_all([
            TransactionImportRow(
                transaction_fact_id=fact.id,
                transaction_import_file_id=file.id,
                source_row_number=1,
                source_reference=f"ref-{file.id}",
                raw_payload="{}",
                raw_hash=str(file.id) * 64,
                row_status=IMPORT_ROW_STATUS_ACCEPTED,
                issue_code="",
                issue_message="",
                created_time=now,
                updated_time=now,
            )
            for file in files
        ])
        db.commit()
        return [file.id for file in files], fact.id


def test_import_file_list_is_a_pure_filterable_po_list(import_file_api):
    client, sessions = import_file_api
    _seed(sessions)

    response = client.get(
        "/paam/import/v1/import_file/list",
        params={
            "filter": '{"op":"AND","expression":[{"key":"source_type","op":"=","val":102},{"key":"status","op":"=","val":1}]}',
            "sorter": '[{"key":"created_time","direction":"asc"}]',
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()["body"]
    assert body["total"] == 1
    assert body["items"][0]["filename"] == "wechat-september.csv"
    assert "account_codes" not in body["items"][0]
    assert set(body["items"][0]) >= {
        "batch_code", "sha256", "period_start", "period_end", "issue_count"
    }


def test_import_file_summary_uses_the_same_search_and_filter_contract(
    import_file_api,
):
    client, sessions = import_file_api
    _seed(sessions)

    response = client.get(
        "/paam/import/v1/import_file/summary",
        params={
            "filter": '{"op":"AND","expression":[{"key":"source_type","op":"=","val":102},{"key":"status","op":"=","val":1}]}',
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == 200
    assert response.json()["body"] == {
        "import_file_count": 1,
        "imported_file_count": 1,
        "row_count": 1,
        "success_count": 1,
        "skip_count": 0,
        "issue_count": 0,
    }


def test_import_file_detail_has_an_unpaged_transaction_fact_subresource(
    import_file_api,
):
    client, sessions = import_file_api
    file_ids, fact_id = _seed(sessions)

    detail = client.get(f"/paam/import/v1/import_file/{file_ids[0]}")
    facts = client.get(f"/paam/import/v1/import_file/{file_ids[0]}/transaction_fact/list")

    assert detail.status_code == 200
    assert detail.json()["body"]["import_file"]["id"] == file_ids[0]
    assert facts.status_code == 200
    assert facts.json()["body"]["total"] == 1
    assert facts.json()["body"]["items"][0]["id"] == fact_id


def test_import_file_rows_show_fact_links_skips_and_raw_json(import_file_api):
    client, sessions = import_file_api
    file_ids, fact_id = _seed(sessions)
    now = datetime(2026, 9, 17, 9)
    with sessions() as db:
        file = db.get(TransactionImportFile, file_ids[0])
        file.total_count = 2
        file.skip_count = 1
        db.add(TransactionImportRow(
            transaction_fact_id=0,
            transaction_import_file_id=file_ids[0],
            source_row_number=2,
            source_reference="zero-amount-row",
            raw_payload='{"normalized":{"action":"record","amount_minor":0},"raw":{"amount":"0.00"}}',
            raw_hash="f" * 64,
            row_status=IMPORT_ROW_STATUS_SKIPPED,
            issue_code="",
            issue_message="",
            created_time=now,
            updated_time=now,
        ))
        db.commit()

    all_rows = client.get(
        f"/paam/import/v1/import_file/{file_ids[0]}/row/list",
        params={"page_size": 1},
    )
    skipped = client.get(
        f"/paam/import/v1/import_file/{file_ids[0]}/row/list",
        params={
            "filter": '{"key":"row_status","op":"=","val":2}',
            "sorter": '[{"key":"source_row_number","direction":"desc"}]',
        },
    )

    assert all_rows.status_code == 200, all_rows.text
    assert all_rows.json()["body"] == {
        "items": [{
            "source_row_number": 1,
            "row_status": IMPORT_ROW_STATUS_ACCEPTED,
            "source_reference": f"ref-{file_ids[0]}",
            "issue_code": "",
            "issue_message": "",
            "raw_payload": "{}",
            "transaction_fact": {
                "id": fact_id,
                "occurred_time": "2026-09-16T08:00:00",
                "cash_direction": CASH_DIRECTION_OUT,
                "amount": 880,
                "currency_code": "CNY",
                "account_code": "wallet",
                "counterparty_name": "Merchant",
                "counterparty_account_ref": "",
                "summary": "Lunch",
                "created_time": "2026-09-16T08:00:00",
                "updated_time": "2026-09-16T08:00:00",
            },
        }],
        "total": 2,
        "page_index": 1,
        "page_size": 1,
    }
    assert skipped.status_code == 200, skipped.text
    skipped_body = skipped.json()["body"]
    assert skipped_body["total"] == 1
    assert skipped_body["items"][0]["source_row_number"] == 2
    assert "transaction_fact" not in skipped_body["items"][0]
    assert '"amount":"0.00"' in skipped_body["items"][0]["raw_payload"]


def test_import_file_row_list_rejects_unknown_filters(import_file_api):
    client, sessions = import_file_api
    file_ids, _fact_id = _seed(sessions)

    response = client.get(
        f"/paam/import/v1/import_file/{file_ids[0]}/row/list",
        params={"filter": '{"key":"sha256","op":"=","val":"hidden"}'},
    )

    assert response.status_code == 422
    assert response.json()["body"]["code"] == "LIST_FILTER_FIELD_NOT_SUPPORTED"


def test_import_file_row_list_query_count_is_fixed(import_file_api):
    client, sessions = import_file_api
    file_ids, _fact_id = _seed(sessions)
    selects = []

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    engine = sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        first = client.get(
            f"/paam/import/v1/import_file/{file_ids[0]}/row/list",
            params={"page_size": 1},
        )
        first_count = len(selects)
        selects.clear()
        second = client.get(
            f"/paam/import/v1/import_file/{file_ids[0]}/row/list",
            params={"page_size": 100},
        )
        second_count = len(selects)
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)

    assert first.status_code == second.status_code == 200
    assert first_count == second_count == 3
    assert all("SELECT *" not in statement.upper() for statement in selects)


def test_import_file_query_validates_generic_objects(import_file_api):
    client, _sessions = import_file_api

    response = client.get(
        "/paam/import/v1/import_file/list",
        params={"filter": '{"account_code":"not-owned-by-import-file"}'},
    )

    assert response.status_code == 422
    assert response.json()["body"]["code"] == "LIST_FILTER_OPERATOR_NOT_SUPPORTED"


def test_import_file_detail_returns_not_found(import_file_api):
    client, _sessions = import_file_api

    response = client.get("/paam/import/v1/import_file/999")

    assert response.status_code == 404
    assert response.json()["status"] == 404
    assert response.json()["body"]["code"] == "INTAKE_ERROR"


def test_file_relation_summary_deduplicates_source_rows_and_excludes_revoked(import_file_api):
    from backend.service.target_economic_service import TargetEconomicService
    from backend.schema.target_review import TargetEconomicReviewCreateRequest, TargetReviewTransitionRequest

    client, sessions = import_file_api
    file_ids, fact_id = _seed(sessions)
    now = datetime(2026, 9, 17, 8)
    with sessions() as db:
        # A repeated source row must not multiply the same fact's money.
        db.add(TransactionImportRow(
            transaction_fact_id=fact_id, transaction_import_file_id=file_ids[0],
            source_row_number=2, row_status=IMPORT_ROW_STATUS_ACCEPTED,
            created_time=now, updated_time=now,
        ))
        precise = TransactionFact(
            fact_key="precision-fact", occurred_time=now, cash_direction=1,
            amount=12345, currency_code="CNY_4", account_code="wallet",
            counterparty_name="Merchant", counterparty_account_ref="", summary="Precise",
            created_time=now, updated_time=now,
        )
        db.add(precise)
        db.flush()
        db.add(TransactionImportRow(
            transaction_fact_id=precise.id, transaction_import_file_id=file_ids[0],
            source_row_number=3, row_status=IMPORT_ROW_STATUS_ACCEPTED,
            created_time=now, updated_time=now,
        ))
        db.commit()
        precise_id = precise.id
        TargetEconomicService(db).ensure_defaults([fact_id, precise_id], commit=True)
    expected = [
        {"currency_code": "CNY", "entry_direction": 2, "amount": 880},
        {"currency_code": "CNY_4", "entry_direction": 1, "amount": 12345},
    ]
    before = client.get(f"/paam/import/v1/import_file/{file_ids[0]}").json()["body"]["relation_summary"]
    assert before["totals"] == expected
    assert before["allocation_count"] == before["ledger_count"] == before["review_count"] == 2
    with sessions() as db:
        review = TargetEconomicService(db).create(TargetEconomicReviewCreateRequest(
            behavior_type=0, title="Split", idempotency_key="file-summary-split",
            economics=[{"client_key": "part", "economic_type": "ACCOUNT_TRANSFER"}],
            allocations=[{"fact_id": fact_id, "economic_key": "part", "amount": 400}],
        ))
        review_id = review.id
    during = client.get(f"/paam/import/v1/import_file/{file_ids[0]}").json()["body"]["relation_summary"]
    assert during["totals"] == expected
    assert during["allocation_count"] == 3
    with sessions() as db:
        TargetEconomicService(db).revoke(review_id, TargetReviewTransitionRequest(
            idempotency_key="file-summary-revoke", actor="test", reason="test",
        ))
    after = client.get(f"/paam/import/v1/import_file/{file_ids[0]}").json()["body"]["relation_summary"]
    assert after["totals"] == expected
    # The untouched 480 residual and restored 400 default remain two entries;
    # revocation does not merge them into a new fabricated aggregate.
    assert after["allocation_count"] == 3
    facts = client.get(f"/paam/import/v1/import_file/{file_ids[0]}/transaction_fact/list").json()["body"]
    assert facts["total"] == 2
