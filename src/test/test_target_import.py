import base64
import csv
import io
import json

import openpyxl
import pyzipper
import pytest
import xlwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.orm import sessionmaker

import backend.service.target_intake_service as target_intake_service_module
from backend.core import target_database
from backend import target_main
from backend.core.intake_preview_store import target_intake_preview_store
from backend.entity import (
    CASH_DIRECTION_IN,
    CASH_DIRECTION_OUT,
    IMPORT_FILE_FORMAT_CSV,
    IMPORT_FILE_STATUS_FAILED,
    IMPORT_FILE_STATUS_IMPORTED,
    IMPORT_FILE_STATUS_PENDING,
    IMPORT_ROW_STATUS_ACCEPTED,
    IMPORT_ROW_STATUS_INVALID,
    IMPORT_ROW_STATUS_SKIPPED,
    IMPORT_SOURCE_ABC_BANK,
    IMPORT_SOURCE_CCB_BANK,
    IMPORT_SOURCE_CMB_BANK,
    IMPORT_SOURCE_WECHAT,
    LedgerEntry,
    ReviewCase,
    TransactionFact,
    TransactionImportFile,
    TransactionImportRow,
)
from backend.mapper.target_import_match_mapper import TargetImportMatchMapper
from backend.mapper.target_import_read_mapper import TargetImportReadMapper
from backend.mapper.target_import_write_mapper import TargetImportWriteMapper
from backend.parser.statement_parser import parse_statement
from backend.service.target_economic_service import TargetEconomicService


HEADERS = [
    "\u4ea4\u6613\u65f6\u95f4",
    "\u4ea4\u6613\u5bf9\u65b9",
    "\u91d1\u989d(\u5143)",
    "\u6536/\u652f",
    "\u4ea4\u6613\u5355\u53f7",
    "\u5f53\u524d\u72b6\u6001",
    "\u652f\u4ed8\u65b9\u5f0f",
    "\u5546\u54c1",
]


@pytest.fixture
def target_import_api(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'target-import.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(target_database, "engine", engine)
    monkeypatch.setattr(target_database, "SessionLocal", sessions)
    target_intake_preview_store.clear()
    try:
        with TestClient(target_main.app) as client:
            yield client, sessions, engine
    finally:
        target_intake_preview_store.clear()
        engine.dispose()


def _rows(count: int = 1, *, amount: str = "10.00", prefix: str = "trade"):
    return [
        [
            f"2026-08-01 12:{index:02d}:00",
            "\u6d4b\u8bd5\u5546\u6237",
            amount,
            "\u652f\u51fa",
            f"{prefix}-{index}",
            "\u652f\u4ed8\u6210\u529f",
            "\u96f6\u94b1",
            "\u6d4b\u8bd5\u5546\u54c1",
        ]
        for index in range(count)
    ]


def _csv(rows=None) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["\u5fae\u4fe1\u652f\u4ed8\u8d26\u5355\u660e\u7ec6"])
    writer.writerow(HEADERS)
    writer.writerows(rows or _rows())
    return output.getvalue().encode()


def _workbook(extension: str) -> bytes:
    rows = _rows(prefix=extension)
    if extension == "xlsx":
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(["\u5fae\u4fe1\u652f\u4ed8\u8d26\u5355\u660e\u7ec6"])
        sheet.append(HEADERS)
        sheet.append(rows[0])
        output = io.BytesIO()
        book.save(output)
        return output.getvalue()
    book = xlwt.Workbook()
    sheet = book.add_sheet("ledger")
    for row_number, values in enumerate([
        ["\u5fae\u4fe1\u652f\u4ed8\u8d26\u5355\u660e\u7ec6"],
        HEADERS,
        rows[0],
    ]):
        for column, value in enumerate(values):
            sheet.write(row_number, column, value)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def _preview(client, filename: str, content: bytes, password: str | None = None):
    response = client.post("/paam/import/v1/preview", json={"files": [{
        "filename": filename,
        "content_base64": base64.b64encode(content).decode(),
        "password": password,
    }]})
    assert response.status_code == 200, response.text
    return response.json()["body"]


def _confirm(client, preview):
    return client.post(
        f"/paam/import/v1/preview/{preview['token']}/confirm",
        json={"version": preview["version"]},
    )


def test_import_read_mapper_owns_public_import_queries():
    for method_name in ("history", "rows", "accounts"):
        assert hasattr(TargetImportReadMapper, method_name)
        assert not hasattr(TargetImportWriteMapper, method_name)


def test_import_match_mapper_owns_preview_planning():
    assert hasattr(TargetImportMatchMapper, "plan")
    assert hasattr(TargetImportMatchMapper, "load_history")
    assert not hasattr(TargetImportWriteMapper, "plan")


def test_import_file_is_pending_before_parser_runs(target_import_api, monkeypatch):
    client, sessions, _ = target_import_api
    original_parse_statement = target_intake_service_module.parse_statement
    observed_statuses = []

    def parse_after_pending(*args, **kwargs):
        with sessions() as db:
            observed_statuses.append(
                db.scalar(select(TransactionImportFile.status))
            )
        return original_parse_statement(*args, **kwargs)

    monkeypatch.setattr(
        target_intake_service_module,
        "parse_statement",
        parse_after_pending,
    )
    preview = _preview(client, "pending-before-parse.csv", _csv())
    assert preview["can_confirm"]
    assert observed_statuses == [IMPORT_FILE_STATUS_PENDING]


def test_target_import_writes_fact_evidence_and_hot_projection(target_import_api):
    client, sessions, engine = target_import_api
    preview = _preview(client, "wechat.csv", _csv())
    assert preview["can_confirm"]
    with sessions() as db:
        pending_file = db.scalar(select(TransactionImportFile))
        pending_file_id = pending_file.id
        assert pending_file.status == IMPORT_FILE_STATUS_PENDING
        assert db.query(TransactionFact).count() == 0
        assert db.query(TransactionImportRow).count() == 0
    response = _confirm(client, preview)
    assert response.status_code == 200, response.text

    with sessions() as db:
        fact = db.scalar(select(TransactionFact))
        assert (
            fact.cash_direction,
            fact.amount,
            fact.currency_code,
            fact.counterparty_name,
            fact.counterparty_account_ref,
        ) == (
            CASH_DIRECTION_OUT,
            1000,
            "CNY",
            "测试商户",
            "",
        )
        import_row = db.scalar(select(TransactionImportRow))
        assert (
            import_row.transaction_fact_id,
            import_row.row_status,
        ) == (
            fact.id,
            IMPORT_ROW_STATUS_ACCEPTED,
        )
        import_file = db.scalar(select(TransactionImportFile))
        assert (
            import_file.id,
            import_file.source_type,
            import_file.file_format,
            import_file.status,
        ) == (
            pending_file_id,
            IMPORT_SOURCE_WECHAT,
            IMPORT_FILE_FORMAT_CSV,
            IMPORT_FILE_STATUS_IMPORTED,
        )
        assert db.query(LedgerEntry).count() == 1
    assert set(inspect(engine).get_table_names()) == set(
        target_database.TARGET_TABLE_NAMES
    )
    ledger_v2 = client.get("/paam/ledger/v1/flow/list")
    assert ledger_v2.status_code == 200, ledger_v2.text
    assert ledger_v2.json()["body"]["total"] == 1
    assert ledger_v2.json()["body"]["items"][0]["entry_type"] == 0
    assert ledger_v2.json()["body"]["items"][0]["amount"] == 1000

    repeated = _preview(client, "renamed.csv", _csv())
    assert repeated["counts"]["duplicate_file"] == 1
    assert _confirm(client, repeated).status_code == 200
    with sessions() as db:
        assert db.query(TransactionFact).count() == 1
        assert db.query(TransactionImportFile).count() == 1


def test_zero_amount_rows_are_preserved_without_creating_facts(target_import_api):
    client, sessions, _ = target_import_api
    rows = _rows(amount="0.00", prefix="zero") + _rows(
        amount="10.00", prefix="normal"
    )
    preview = _preview(client, "zero-balance-opening.csv", _csv(rows))

    assert preview["can_confirm"]
    assert preview["counts"]["record"] == 1
    assert preview["counts"]["new"] == 1

    response = _confirm(client, preview)
    assert response.status_code == 200, response.text
    with sessions() as db:
        assert db.query(TransactionFact).count() == 1
        assert db.query(TransactionImportRow).count() == 2
        skipped = db.scalar(select(TransactionImportRow).where(
            TransactionImportRow.row_status == IMPORT_ROW_STATUS_SKIPPED
        ))
        assert skipped is not None
        assert skipped.transaction_fact_id == 0
        assert db.query(LedgerEntry).count() == 1


def test_target_import_rolls_back_when_default_review_write_fails(
    target_import_api,
    monkeypatch,
):
    client, sessions, _ = target_import_api
    preview = _preview(client, "rollback.csv", _csv())

    original_ensure_defaults = TargetEconomicService.ensure_defaults

    def fail_after_default_review(service, fact_ids):
        original_ensure_defaults(service, fact_ids)
        raise RuntimeError("simulated default review failure")

    monkeypatch.setattr(
        TargetEconomicService,
        "ensure_defaults",
        fail_after_default_review,
    )
    response = _confirm(client, preview)
    assert response.status_code == 500

    with sessions() as db:
        assert db.query(TransactionFact).count() == 0
        import_file = db.scalar(select(TransactionImportFile))
        failed_file_id = import_file.id
        assert import_file.status == IMPORT_FILE_STATUS_FAILED
        assert db.query(TransactionImportRow).count() == 0
        assert db.query(ReviewCase).count() == 0
        assert db.query(LedgerEntry).count() == 0

    monkeypatch.setattr(
        TargetEconomicService,
        "ensure_defaults",
        original_ensure_defaults,
    )
    retried = _confirm(client, preview)
    assert retried.status_code == 200, retried.text
    assert retried.json()["body"]["transaction_import_file_ids"] == [
        failed_file_id
    ]


def test_import_history_supports_canonical_pagination_and_source_filter(
    target_import_api,
):
    client, _, _ = target_import_api
    cash_rows = _rows(prefix="cash")
    card_rows = _rows(prefix="card")
    card_rows[0][6] = "建设银行储蓄卡(1234)"
    for filename, rows in [
        ("cash-august.csv", cash_rows),
        ("card-september.csv", card_rows),
    ]:
        preview = _preview(client, filename, _csv(rows))
        assert _confirm(client, preview).status_code == 200

    first_page = client.get(
        "/paam/import/v1/import_file/list?page_index=1&page_size=1"
    ).json()["body"]
    assert (first_page["total"], len(first_page["items"])) == (2, 1)
    assert first_page["items"][0]["filename"] == "card-september.csv"
    second_page = client.get(
        "/paam/import/v1/import_file/list?page_index=2&page_size=1"
    ).json()["body"]
    assert second_page["items"][0]["filename"] == "cash-august.csv"

    source_search = client.get(
        "/paam/import/v1/import_file/list",
        params={"filter": '{"key":"source_type","op":"=","val":102}'},
    ).json()["body"]
    assert source_search["total"] == 2

    unsupported_search = client.get(
        "/paam/import/v1/import_file/list",
        params={"q": "card-september"},
    )
    assert unsupported_search.status_code == 422
    assert "account_codes" not in first_page["items"][0]


def test_import_history_preserves_specific_bank_source_codes(target_import_api):
    client, sessions, _ = target_import_api
    sources = [
        (IMPORT_SOURCE_CCB_BANK, "ccb", "建设银行"),
        (IMPORT_SOURCE_ABC_BANK, "abc", "农业银行"),
        (IMPORT_SOURCE_CMB_BANK, "cmb", "招商银行"),
    ]
    with sessions() as db:
        db.add_all([
            TransactionImportFile(
                batch_code=f"bank-{name}",
                source_type=code,
                filename=f"{name}.csv",
                file_format=IMPORT_FILE_FORMAT_CSV,
                sha256=f"{code:064x}",
                total_count=0,
                success_count=0,
                skip_count=0,
                issue_count=0,
                status=IMPORT_FILE_STATUS_IMPORTED,
            )
            for code, name, _label in sources
        ])
        db.commit()

    history = client.get(
        "/paam/import/v1/import_file/list", params={"page_size": 100}
    ).json()["body"]
    assert {item["source_type"] for item in history["items"]} == {
        IMPORT_SOURCE_CCB_BANK,
        IMPORT_SOURCE_ABC_BANK,
        IMPORT_SOURCE_CMB_BANK,
    }
    for code, _name, _label in sources:
        filtered = client.get(
            "/paam/import/v1/import_file/list",
            params={"filter": f'{{"key":"source_type","op":"=","val":{code}}}'},
        ).json()["body"]
        assert [item["source_type"] for item in filtered["items"]] == [code]


def test_import_history_child_returns_all_related_rows(target_import_api):
    client, _, _ = target_import_api
    preview = _preview(
        client,
        "history-detail.csv",
        _csv(_rows(26, prefix="history-detail")),
    )
    confirmed = _confirm(client, preview)
    assert confirmed.status_code == 200, confirmed.text
    batch_id = confirmed.json()["body"]["transaction_import_file_ids"][0]

    first = client.get(
        f"/paam/import/v1/import_file/{batch_id}/transaction_fact/list"
    ).json()["body"]
    assert (first["total"], len(first["items"])) == (26, 26)
    assert first["items"][0]["id"] > 0
    assert first["items"][0]["cash_direction"] in {
        CASH_DIRECTION_IN,
        CASH_DIRECTION_OUT,
    }

    unsupported_page = client.get(
        f"/paam/import/v1/import_file/{batch_id}/transaction_fact/list",
        params={"page_index": 2},
    )
    assert unsupported_page.status_code == 422


def test_target_confirm_select_count_is_independent_of_row_count(target_import_api):
    client, _, engine = target_import_api

    def count_for(row_count: int, prefix: str) -> int:
        preview = _preview(client, f"{prefix}.csv", _csv(_rows(row_count, prefix=prefix)))
        statements = []

        def listener(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", listener)
        try:
            response = _confirm(client, preview)
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        assert response.status_code == 200, response.text
        assert all("SELECT *" not in statement.upper() for statement in statements)
        return len(statements)

    one = count_for(1, "one")
    twenty = count_for(20, "twenty")
    assert twenty <= one + 2


@pytest.mark.parametrize("extension", ["xls", "xlsx"])
def test_target_import_accepts_supported_workbooks(target_import_api, extension):
    client, sessions, _ = target_import_api
    preview = _preview(client, f"statement.{extension}", _workbook(extension))
    assert preview["can_confirm"]
    assert _confirm(client, preview).status_code == 200
    with sessions() as db:
        assert db.query(TransactionFact).count() == 1


def test_encrypted_zip_password_is_ephemeral(target_import_api):
    client, sessions, _ = target_import_api
    password = "test-only-password"
    output = io.BytesIO()
    with pyzipper.AESZipFile(
        output,
        "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as archive:
        archive.setpassword(password.encode())
        archive.writestr("wechat.csv", _csv())

    wrong = _preview(client, "statement.zip", output.getvalue(), "wrong")
    assert not wrong["can_confirm"]
    assert password not in json.dumps(wrong, ensure_ascii=False)
    with sessions() as db:
        failed_file = db.scalar(select(TransactionImportFile))
        failed_file_id = failed_file.id
        assert failed_file.status == IMPORT_FILE_STATUS_FAILED
    preview = _preview(client, "statement.zip", output.getvalue(), password)
    assert preview["can_confirm"]
    with sessions() as db:
        pending_file = db.scalar(select(TransactionImportFile))
        assert (pending_file.id, pending_file.status) == (
            failed_file_id,
            IMPORT_FILE_STATUS_PENDING,
        )
    assert _confirm(client, preview).status_code == 200
    with sessions() as db:
        raw = db.scalar(select(TransactionImportRow.raw_payload))
        assert password not in raw
        assert db.scalar(
            select(TransactionImportFile.file_format)
        ) == IMPORT_FILE_FORMAT_CSV
        assert db.query(TransactionImportFile).count() == 1


@pytest.mark.parametrize("extension", ["xls", "xlsx"])
def test_corrupt_workbook_is_a_preview_error(target_import_api, extension):
    client, sessions, _ = target_import_api
    preview = _preview(client, f"bad.{extension}", b"not a workbook")
    assert not preview["can_confirm"]
    assert preview["documents"][0]["error"]
    with sessions() as db:
        assert db.query(TransactionFact).count() == 0
        import_file = db.scalar(select(TransactionImportFile))
        assert import_file.status == IMPORT_FILE_STATUS_FAILED


def test_target_fact_conflict_stays_on_import_row(target_import_api):
    client, sessions, _ = target_import_api
    first = _preview(client, "first.csv", _csv())
    assert _confirm(client, first).status_code == 200
    conflict_rows = _rows(amount="20.00")
    conflict = _preview(client, "conflict.csv", _csv(conflict_rows))
    assert conflict["counts"]["error"] == 1
    assert _confirm(client, conflict).status_code == 200

    with sessions() as db:
        raw = db.scalar(select(TransactionImportRow).where(
            TransactionImportRow.issue_code == "FACT_CONFLICT"
        ))
        assert (raw.row_status, raw.transaction_fact_id) == (
            IMPORT_ROW_STATUS_INVALID,
            0,
        )
        assert db.query(ReviewCase).count() == 1


def test_source_override_cannot_contradict_statement_content():
    with pytest.raises(ValueError):
        parse_statement(_csv(), "statement.csv", source="alipay")
