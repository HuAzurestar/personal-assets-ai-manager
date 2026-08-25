import io
import json

import openpyxl
import pyzipper
import pytest
import xlwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, ImportArtifact, LedgerOrigin
from app.main import app, get_db

MAPPING = {
    "occurred_at": "time",
    "merchant": "merchant",
    "amount": "amount",
    "note": "note",
    "direction": None,
    "reference": "reference",
}
CSV_BYTES = b"time,merchant,amount,note,reference\n2026-08-25 10:00:00,Sample Merchant,-12.50,Sample note,ref-1\n"
TEST_PASSWORD = "test-only-password"


@pytest.fixture
def client_and_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'import.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            yield client, session_factory
    finally:
        app.dependency_overrides.clear()


def _headers(password: str | None = None):
    headers = {"X-Import-Mapping": json.dumps(MAPPING)}
    if password:
        headers["X-Import-Password"] = password
    return headers


def _xlsx_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["time", "merchant", "amount", "note", "reference"])
    sheet.append(["2026-08-25 11:00:00", "XLSX Merchant", -20, "xlsx", "xlsx-1"])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _xls_bytes() -> bytes:
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet("ledger")
    for column, value in enumerate(["time", "merchant", "amount", "note", "reference"]):
        sheet.write(0, column, value)
    for column, value in enumerate(["2026-08-25 12:00:00", "XLS Merchant", -30, "xls", "xls-1"]):
        sheet.write(1, column, value)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _encrypted_zip() -> bytes:
    output = io.BytesIO()
    with pyzipper.AESZipFile(output, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as archive:
        archive.setpassword(TEST_PASSWORD.encode())
        archive.writestr("ledger.csv", CSV_BYTES)
    return output.getvalue()


def test_csv_preview_mapping_commit_and_duplicate_block(client_and_session):
    client, _ = client_and_session
    preview = client.post("/api/imports/alipay/preview?filename=sample.csv", content=CSV_BYTES)
    assert preview.status_code == 200
    assert preview.json()["file_format"] == "csv"
    assert preview.json()["columns"] == ["time", "merchant", "amount", "note", "reference"]
    committed = client.post("/api/imports/alipay?filename=sample.csv", content=CSV_BYTES, headers=_headers())
    assert committed.status_code == 201
    assert len(committed.json()["file_sha256"]) == 64
    assert client.post("/api/imports/alipay?filename=renamed.csv", content=CSV_BYTES, headers=_headers()).status_code == 409
    bills = client.get("/api/bills").json()
    assert len(bills) == 1
    assert bills[0]["source_type"] == "alipay"


@pytest.mark.parametrize(("filename", "payload", "expected_format"), [("sample.xlsx", _xlsx_bytes(), "xlsx"), ("sample.xls", _xls_bytes(), "xls")])
def test_xlsx_and_xls_preview_and_import(client_and_session, filename, payload, expected_format):
    client, _ = client_and_session
    preview = client.post(f"/api/imports/wechat/preview?filename={filename}", content=payload)
    assert preview.status_code == 200
    assert preview.json()["file_format"] == expected_format
    committed = client.post(f"/api/imports/wechat?filename={filename}", content=payload, headers=_headers())
    assert committed.status_code == 201
    assert committed.json()["imported_count"] == 1


def test_password_zip_invalid_password_and_no_password_persistence(client_and_session):
    client, session_factory = client_and_session
    payload = _encrypted_zip()
    wrong = client.post("/api/imports/wechat/preview?filename=sample.zip", content=payload, headers=_headers("wrong-password"))
    assert wrong.status_code == 422
    assert TEST_PASSWORD not in wrong.text
    preview = client.post("/api/imports/wechat/preview?filename=sample.zip", content=payload, headers=_headers(TEST_PASSWORD))
    assert preview.status_code == 200
    assert preview.json()["archive_entry"] == "ledger.csv"
    committed = client.post("/api/imports/wechat?filename=sample.zip", content=payload, headers=_headers(TEST_PASSWORD))
    assert committed.status_code == 201
    with session_factory() as db:
        artifact = db.scalar(select(ImportArtifact))
        assert artifact.filename == "ledger.csv"
        assert artifact.sha256
        assert TEST_PASSWORD not in str(artifact)
        origin = db.scalar(select(LedgerOrigin))
        assert TEST_PASSWORD not in origin.raw_payload


def test_rejects_unsupported_and_unsafe_archives(client_and_session):
    client, _ = client_and_session
    assert client.post("/api/imports/alipay/preview?filename=sample.pdf", content=b"not a ledger").status_code == 422
    output = io.BytesIO()
    with pyzipper.AESZipFile(output, "w") as archive:
        archive.writestr("first.csv", CSV_BYTES)
        archive.writestr("second.csv", CSV_BYTES)
    assert client.post("/api/imports/alipay/preview?filename=multi.zip", content=output.getvalue()).status_code == 422


def test_import_ui_uses_a_one_request_password_field_without_browser_storage(client_and_session):
    client, _ = client_and_session
    script = client.get("/static/workbench.js").text
    assert "X-Import-Password" in script
    assert "clearImportPassword" in script
    assert "localStorage" not in script
