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

from backend.core import target_database
from backend import target_main
from backend.core.intake_preview_store import target_intake_preview_store
from backend.entity import BillFact, BillRaw, ImportFile, LedgerEntry, ReviewCase
from backend.parser.statement_parser import parse_statement


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


def test_target_import_writes_fact_evidence_and_hot_projection(target_import_api):
    client, sessions, engine = target_import_api
    preview = _preview(client, "wechat.csv", _csv())
    assert preview["can_confirm"]
    response = _confirm(client, preview)
    assert response.status_code == 200, response.text

    with sessions() as db:
        fact = db.scalar(select(BillFact))
        assert (fact.amount_value, fact.amount_scale, fact.currency_code) == (
            1000,
            2,
            "CNY",
        )
        assert db.query(BillRaw).count() == 1
        assert db.query(ImportFile).count() == 1
        assert db.query(LedgerEntry).count() == 1
    assert set(inspect(engine).get_table_names()) == set(
        target_database.TARGET_TABLE_NAMES
    )
    ledger = client.get("/paam/ledger/v1/flow/list")
    assert ledger.status_code == 200, ledger.text
    assert ledger.json()["status"] == ledger.status_code
    ledger_page = ledger.json()["body"]
    assert ledger_page["total"] == 1
    assert ledger_page["items"][0]["economic_type"] == "TRANSACTION"
    assert ledger_page["items"][0]["amount"]["amount_value"] == 1000

    repeated = _preview(client, "renamed.csv", _csv())
    assert repeated["counts"]["duplicate_file"] == 1
    assert _confirm(client, repeated).status_code == 200
    with sessions() as db:
        assert db.query(BillFact).count() == 1


def test_import_file_api_supports_history_search_filter_and_summary(
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
        "/paam/import/v1/import_file/list",
        params={
            "page": 1,
            "page_size": 1,
            "sorter": '{"field":"created_time","order":"desc"}',
        },
    ).json()["body"]
    assert (first_page["total"], len(first_page["items"])) == (2, 1)
    assert first_page["items"][0]["filename"] == "card-september.csv"
    assert "account_codes" not in first_page["items"][0]

    second_page = client.get(
        "/paam/import/v1/import_file/list",
        params={
            "page": 2,
            "page_size": 1,
            "sorter": '{"field":"created_time","order":"desc"}',
        },
    ).json()["body"]
    assert second_page["items"][0]["filename"] == "cash-august.csv"

    search = client.get(
        "/paam/import/v1/import_file/list", params={"q": "september"}
    ).json()["body"]
    assert [item["filename"] for item in search["items"]] == [
        "card-september.csv"
    ]
    filtered = client.get(
        "/paam/import/v1/import_file/list",
        params={"filter": '{"source_type":"wechat","status":"IMPORTED"}'},
    ).json()["body"]
    assert filtered["total"] == 2

    summary = client.get(
        "/paam/import/v1/import_file/summary",
        params={"filter": '{"source_type":"wechat","status":"IMPORTED"}'},
    ).json()["body"]
    assert summary["import_file_count"] == 2
    assert summary["imported_file_count"] == 2
    assert summary["row_count"] == 2
    assert summary["success_count"] == 2
    assert client.get("/paam/import/v1/batch/list").status_code == 404
    assert client.get("/paam/import/v1/account/list").status_code == 404


def test_import_file_transaction_facts_are_loaded_by_page(target_import_api):
    client, _, _ = target_import_api
    preview = _preview(
        client,
        "history-detail.csv",
        _csv(_rows(26, prefix="history-detail")),
    )
    confirmed = _confirm(client, preview)
    assert confirmed.status_code == 200, confirmed.text
    batch_id = confirmed.json()["body"]["import_file_ids"][0]

    first = client.get(
        f"/paam/import/v1/import_file/{batch_id}/transaction_fact/list",
        params={"page": 1, "page_size": 20},
    ).json()["body"]
    assert (first["total"], len(first["items"])) == (26, 20)

    second = client.get(
        f"/paam/import/v1/import_file/{batch_id}/transaction_fact/list",
        params={"page": 2, "page_size": 20},
    ).json()["body"]
    assert (second["page"], len(second["items"])) == (2, 6)
    assert second["items"][-1]["id"] < first["items"][0]["id"]
    assert client.get(
        f"/paam/import/v1/batch/{batch_id}/row/list"
    ).status_code == 404


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
        assert db.query(BillFact).count() == 1


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
    preview = _preview(client, "statement.zip", output.getvalue(), password)
    assert preview["can_confirm"]
    assert _confirm(client, preview).status_code == 200
    with sessions() as db:
        raw = db.scalar(select(BillRaw.raw_payload))
        assert password not in raw


@pytest.mark.parametrize("extension", ["xls", "xlsx"])
def test_corrupt_workbook_is_a_preview_error(target_import_api, extension):
    client, sessions, _ = target_import_api
    preview = _preview(client, f"bad.{extension}", b"not a workbook")
    assert not preview["can_confirm"]
    assert preview["documents"][0]["error"]
    with sessions() as db:
        assert db.query(BillFact).count() == 0


def test_target_fact_conflict_is_recorded_for_review(target_import_api):
    client, sessions, _ = target_import_api
    first = _preview(client, "first.csv", _csv())
    assert _confirm(client, first).status_code == 200
    conflict_rows = _rows(amount="20.00")
    conflict = _preview(client, "conflict.csv", _csv(conflict_rows))
    assert conflict["counts"]["error"] == 1
    assert _confirm(client, conflict).status_code == 200

    with sessions() as db:
        raw = db.scalar(select(BillRaw).where(BillRaw.issue_code == "FACT_CONFLICT"))
        case = db.scalar(select(ReviewCase).where(
            ReviewCase.review_type == "FACT_CONFLICT"
        ))
        assert (raw.parse_status, raw.bill_id) == ("INVALID", 0)
        assert case.status == "PENDING"

    conflict_page_response = client.get(
        "/paam/import/v1/fact_conflict/list",
        params={
            "page": 1,
            "page_size": 25,
            "filter": '{"status":"PENDING"}',
            "sorter": '{"field":"id","order":"asc"}',
        },
    )
    assert conflict_page_response.status_code == 200, conflict_page_response.text
    assert conflict_page_response.json()["status"] == 200
    conflict_page = conflict_page_response.json()["body"]
    assert (conflict_page["total"], conflict_page["page"]) == (1, 1)
    assert [item["id"] for item in conflict_page["items"]] == [case.id]
    assert conflict_page["filter"] == {"status": "PENDING"}
    assert conflict_page["sorter"] == {"field": "id", "order": "asc"}

    conflict_detail_response = client.get(
        f"/paam/import/v1/fact_conflict/{case.id}"
    )
    assert conflict_detail_response.status_code == 200, conflict_detail_response.text
    conflict_detail = conflict_detail_response.json()["body"]
    assert conflict_detail["review_type"] == "FACT_CONFLICT"
    assert conflict_detail["status"] == "PENDING"
    assert conflict_detail["history"][0]["operation"] == "CREATE"
    assert client.get("/paam/review/v1/case/page").status_code == 404
    assert client.get(
        f"/paam/review/v1/case/detail/{case.id}"
    ).status_code == 404
    assert client.post(
        f"/paam/import/v1/fact-conflict/{case.id}/resolve",
        json={},
    ).status_code == 404

    resolved = client.post(
        f"/paam/import/v1/fact_conflict/{case.id}/resolve",
        json={
            "resolution_type": "CREATE_NEW",
            "expected_version": 1,
            "reason": "verified separate transaction",
            "idempotency_key": "fact-conflict-create-new",
        },
    )
    assert resolved.status_code == 200, resolved.text
    with sessions() as db:
        assert [item.amount_value for item in db.scalars(
            select(BillFact).order_by(BillFact.id)
        )] == [1000, 2000]
        assert db.query(LedgerEntry).count() == 2


def test_source_override_cannot_contradict_statement_content():
    with pytest.raises(ValueError):
        parse_statement(_csv(), "statement.csv", source="alipay")
