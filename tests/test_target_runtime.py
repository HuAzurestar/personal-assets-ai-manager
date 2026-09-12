import base64
import csv
import io

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app import database, target_main
from app.core.intake_preview_store import target_intake_preview_store


def _wechat_csv() -> bytes:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["微信支付账单明细"])
    writer.writerow([
        "交易时间",
        "交易对方",
        "金额(元)",
        "收/支",
        "交易单号",
        "当前状态",
        "支付方式",
    ])
    writer.writerow([
        "2026-08-01 12:00:00",
        "测试商户",
        "10.00",
        "支出",
        "target-runtime-1",
        "支付成功",
        "零钱",
    ])
    return stream.getvalue().encode()


def test_target_runtime_uses_only_pirc9_tables_and_routes(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'runtime.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    target_intake_preview_store.clear()
    try:
        with TestClient(target_main.app) as client:
            assert client.get("/api/health").json() == {
                "status": "ok",
                "schema": "pirc-9-target",
            }
            preview_response = client.post(
                "/paam/import/v1/preview",
                json={"files": [{
                    "filename": "wechat.csv",
                    "content_base64": base64.b64encode(_wechat_csv()).decode(),
                }]},
            )
            assert preview_response.status_code == 200, preview_response.text
            preview = preview_response.json()["body"]
            confirmation = client.post(
                f"/paam/import/v1/preview/confirm/{preview['token']}",
                json={"version": preview["version"]},
            )
            assert confirmation.status_code == 200, confirmation.text
            page = client.get("/paam/ledger/v1/entry/list")
            assert page.status_code == 200, page.text
            assert page.json()["total"] == 1
            assert client.get("/api/intake/history").status_code == 404
            assert client.get("/api/shadow/v1/ledger/status").status_code == 404

        assert set(inspect(engine).get_table_names()) == set(database.TARGET_TABLE_NAMES)
    finally:
        target_intake_preview_store.clear()
        engine.dispose()
