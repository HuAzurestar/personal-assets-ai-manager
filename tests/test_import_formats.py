import io
import base64

import openpyxl
import pyzipper
import pytest
import xlwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, ImportArtifact, ImportBatch, LedgerOrigin
from app.main import app, get_db

ALIPAY_CSV = "支付宝交易明细查询结果\n导出时间：2026-08-25\n交易号,商家订单号,交易创建时间,付款时间,交易对方,商品名称,金额（元）,收/支,交易状态,备注\nali-001,order-1,2026-08-25 10:00:00,2026-08-25 10:01:00,脱敏商户,午餐,12.50,支出,交易成功,测试账单\n".encode("gb18030")
WECHAT_CSV = "微信支付账单明细列表\n账单时间：2026-08-25\n交易时间,交易类型,交易对手,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注\n2026-08-25 11:00:00,商户消费,脱敏微信商户,咖啡,支出,20.00,零钱,支付成功,wx-001,mch-1,测试账单\n".encode()
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
    headers = {}
    if password:
        headers["X-Import-Password"] = password
    return headers


def _xlsx_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["微信支付账单明细列表"])
    sheet.append(["交易时间", "交易类型", "交易对手", "商品", "收/支", "金额(元)", "支付方式", "当前状态", "交易单号", "商户单号", "备注"])
    sheet.append(["2026-08-25 11:00:00", "商户消费", "脱敏微信商户", "咖啡", "支出", -20, "零钱", "支付成功", "xlsx-1", "mch-xlsx", "xlsx"])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _xls_bytes() -> bytes:
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet("ledger")
    sheet.write(0, 0, "微信支付账单明细列表")
    for column, value in enumerate(["交易时间", "交易类型", "交易对手", "商品", "收/支", "金额(元)", "支付方式", "当前状态", "交易单号", "商户单号", "备注"]):
        sheet.write(1, column, value)
    for column, value in enumerate(["2026-08-25 12:00:00", "商户消费", "脱敏微信商户", "午餐", "支出", -30, "零钱", "支付成功", "xls-1", "mch-xls", "xls"]):
        sheet.write(2, column, value)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _encrypted_zip() -> bytes:
    output = io.BytesIO()
    with pyzipper.AESZipFile(output, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as archive:
        archive.setpassword(TEST_PASSWORD.encode())
        archive.writestr("wechat.csv", WECHAT_CSV)
    return output.getvalue()


def _batch_file(filename: str, payload: bytes) -> dict[str, str]:
    return {"filename": filename, "content_base64": base64.b64encode(payload).decode()}


def test_alipay_template_skips_preamble_and_confirms_without_a_mapping_header(client_and_session):
    client, _ = client_and_session
    preview = client.post("/api/imports/alipay/preview?filename=alipay.csv", content=ALIPAY_CSV)
    assert preview.status_code == 200
    assert preview.json()["file_format"] == "csv"
    assert preview.json()["columns"] == ["交易时间", "交易方", "金额", "备注", "收支", "流水号"]
    assert preview.json()["preview_rows"][0]["交易方"] == "脱敏商户"
    committed = client.post("/api/imports/alipay?filename=alipay.csv", content=ALIPAY_CSV)
    assert committed.status_code == 201
    assert len(committed.json()["file_sha256"]) == 64
    assert client.post("/api/imports/alipay?filename=renamed.csv", content=ALIPAY_CSV).status_code == 409
    bills = client.get("/api/bills").json()
    assert len(bills) == 1
    assert bills[0]["source_type"] == "alipay"


@pytest.mark.parametrize(("filename", "payload", "expected_format"), [("sample.xlsx", _xlsx_bytes(), "xlsx"), ("sample.xls", _xls_bytes(), "xls")])
def test_xlsx_and_xls_preview_and_import(client_and_session, filename, payload, expected_format):
    client, _ = client_and_session
    preview = client.post(f"/api/imports/wechat/preview?filename={filename}", content=payload)
    assert preview.status_code == 200
    assert preview.json()["file_format"] == expected_format
    committed = client.post(f"/api/imports/wechat?filename={filename}", content=payload)
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
    assert preview.json()["archive_entry"] == "wechat.csv"
    committed = client.post("/api/imports/wechat?filename=sample.zip", content=payload, headers=_headers(TEST_PASSWORD))
    assert committed.status_code == 201
    with session_factory() as db:
        artifact = db.scalar(select(ImportArtifact))
        assert artifact.filename == "sample.zip"
        assert artifact.sha256
        assert TEST_PASSWORD not in str(artifact)
        origin = db.scalar(select(LedgerOrigin))
        assert TEST_PASSWORD not in origin.raw_payload


def test_rejects_unsupported_and_unsafe_archives(client_and_session):
    client, _ = client_and_session
    assert client.post("/api/imports/alipay/preview?filename=sample.pdf", content=b"not a ledger").status_code == 422
    output = io.BytesIO()
    with pyzipper.AESZipFile(output, "w") as archive:
        archive.writestr("first.csv", ALIPAY_CSV)
        archive.writestr("second.csv", ALIPAY_CSV)
    assert client.post("/api/imports/alipay/preview?filename=multi.zip", content=output.getvalue()).status_code == 422


def test_batch_preview_and_import_preserves_one_batch_token_and_skips_duplicates(client_and_session):
    client, session_factory = client_and_session
    payload = {"batch_token": "fixture-batch-001", "files": [_batch_file("first.csv", ALIPAY_CSV), _batch_file("second.csv", ALIPAY_CSV.replace(b"ali-001", b"ali-002"))]}
    preview = client.post("/api/imports/alipay/batch/preview", json=payload)
    assert preview.status_code == 200
    assert [item["ok"] for item in preview.json()["files"]] == [True, True]
    committed = client.post("/api/imports/alipay/batch", json=payload)
    assert committed.status_code == 201
    assert [item["status"] for item in committed.json()["files"]] == ["imported", "imported"]
    with session_factory() as db:
        assert {batch.batch_token for batch in db.scalars(select(ImportBatch)).all()} == {"fixture-batch-001"}
    duplicate = client.post("/api/imports/alipay/batch", json=payload)
    assert [item["status"] for item in duplicate.json()["files"]] == ["duplicate", "duplicate"]


def test_import_ui_uses_a_one_request_password_field_without_browser_storage(client_and_session):
    client, _ = client_and_session
    script = client.get("/static/workbench.js").text
    assert "X-Import-Password" in script
    assert "clearImportPassword" in script
    assert "localStorage" not in script
    assert "webkitdirectory" in script
    assert "/batch/preview" in script
    assert "data-map-field" not in script
    assert "data-import=" not in script
