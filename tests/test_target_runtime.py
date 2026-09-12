import base64
import csv
import io
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app import target_database, target_main
from app.core.intake_preview_store import target_intake_preview_store


ROOT = Path(__file__).resolve().parents[1]


def test_importing_target_runtime_does_not_load_legacy_database_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import app.target_main; "
                "assert 'app.database' not in sys.modules"
            ),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _wechat_csv(rows=None) -> bytes:
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
    for row in rows or [[
        "2026-08-01 12:00:00", "测试商户", "10.00", "支出",
        "target-runtime-1", "支付成功", "零钱",
    ]]:
        writer.writerow(row)
    return stream.getvalue().encode()


def test_target_runtime_uses_only_pirc9_tables_and_routes(tmp_path, monkeypatch):
    assert 'uvicorn.run("app.target_main:app"' in (ROOT / "run.py").read_text(
        encoding="utf-8"
    )
    engine = create_engine(
        f"sqlite:///{tmp_path / 'runtime.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(target_database, "engine", engine)
    monkeypatch.setattr(target_database, "SessionLocal", sessions)
    target_intake_preview_store.clear()
    try:
        with TestClient(target_main.app) as client:
            assert client.get("/api/health").json() == {
                "status": "ok",
                "schema": "pirc-9-target",
            }
            home = client.get("/")
            assert home.status_code == 200
            assert "/static/target-ledger.js" in home.text
            assert "/static/ledger.js" not in home.text
            script = client.get("/static/target-ledger.js")
            assert script.status_code == 200
            assert "/paam/ledger/v1/" in script.text
            assert "/paam/import/v1/preview/" in script.text
            for legacy_path in (
                "/api/transactions",
                "/api/dashboard",
                "/api/tag-views",
                "/api/candidates",
                "/api/intake",
            ):
                assert legacy_path not in script.text
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

        assert set(inspect(engine).get_table_names()) == set(
            target_database.TARGET_TABLE_NAMES
        )
    finally:
        target_intake_preview_store.clear()
        engine.dispose()


def test_target_review_confirm_revoke_restore_rebuilds_projection(
    tmp_path,
    monkeypatch,
):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'review-runtime.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(target_database, "engine", engine)
    monkeypatch.setattr(target_database, "SessionLocal", sessions)
    target_intake_preview_store.clear()
    try:
        with TestClient(target_main.app) as client:
            content = _wechat_csv([
                ["2026-08-01 12:00:00", "本人招行", "10.00", "支出", "transfer-out", "支付成功", "零钱"],
                ["2026-08-01 12:01:00", "本人招行", "9.99", "收入", "transfer-in", "支付成功", "零钱"],
            ])
            preview = client.post(
                "/paam/import/v1/preview",
                json={"files": [{
                    "filename": "transfer.csv",
                    "content_base64": base64.b64encode(content).decode(),
                }]},
            ).json()["body"]
            confirmation = client.post(
                f"/paam/import/v1/preview/confirm/{preview['token']}",
                json={"version": preview["version"]},
            )
            assert confirmation.status_code == 200, confirmation.text
            fact_ids = confirmation.json()["body"]["bill_fact_ids"]
            assert len(fact_ids) == 2

            created = client.post(
                "/paam/review/v1/case/create",
                json={
                    "review_type": "TRANSFER",
                    "title": "零钱转入招行",
                    "lines": [
                        {"bill_id": fact_ids[0], "role": "TRANSFER_OUT"},
                        {"bill_id": fact_ids[1], "role": "TRANSFER_IN"},
                    ],
                    "reason": "识别本人账户转账",
                    "idempotency_key": "create-transfer-1",
                },
            )
            assert created.status_code == 200, created.text
            case = created.json()["body"]
            assert case["status"] == "PENDING"
            assert case["version"] == 1
            assert [line["amount_value"] for line in case["lines"]] == [1000, 999]
            assert case["history"][0]["operation"] == "CREATE"

            confirmed = client.post(
                f"/paam/review/v1/case/confirm/{case['id']}",
                json={
                    "expected_version": 1,
                    "reason": "确认本人转账",
                    "idempotency_key": "confirm-transfer-1",
                },
            )
            assert confirmed.status_code == 200, confirmed.text
            case = confirmed.json()["body"]
            assert case["status"] == "CONFIRMED"
            assert case["version"] == 2
            assert [item["operation"] for item in case["history"]] == ["CREATE", "CONFIRM"]
            page = client.get("/paam/ledger/v1/entry/list").json()
            assert page["total"] == 1
            assert page["items"][0]["ledger_type"] == "TRANSFER"
            assert page["items"][0]["incoming"]["amount_value"] == 999
            assert page["items"][0]["outgoing"]["amount_value"] == 1000
            detail = client.get(
                f"/paam/ledger/v1/entry/detail/{page['items'][0]['id']}"
            ).json()
            assert len(detail["facts"]) == 2
            assert len(detail["raw_evidence"]) == 2
            assert detail["reviews"][0]["is_projection_source"] is True
            assert [
                item["operation"] for item in detail["reviews"][0]["history"]
            ] == ["CREATE", "CONFIRM"]
            summary = client.get("/paam/ledger/v1/summary").json()
            assert summary["totals"][0]["expense_value"] == 1

            replay = client.post(
                f"/paam/review/v1/case/confirm/{case['id']}",
                json={
                    "expected_version": 1,
                    "reason": "确认本人转账",
                    "idempotency_key": "confirm-transfer-1",
                },
            )
            assert replay.status_code == 200
            assert replay.json() == confirmed.json()

            revoked = client.post(
                f"/paam/review/v1/case/revoke/{case['id']}",
                json={
                    "expected_version": 2,
                    "reason": "撤销核查",
                    "idempotency_key": "revoke-transfer-1",
                },
            )
            assert revoked.status_code == 200, revoked.text
            case = revoked.json()["body"]
            assert case["status"] == "REVOKED"
            assert case["version"] == 3
            assert case["history"][-1]["reverses_history_id"] == case["history"][1]["id"]
            page = client.get("/paam/ledger/v1/entry/list").json()
            assert page["total"] == 2
            assert {item["ledger_type"] for item in page["items"]} == {"INCOME", "EXPENSE"}

            restored = client.post(
                f"/paam/review/v1/case/restore/{case['id']}",
                json={
                    "expected_version": 3,
                    "reason": "恢复核查",
                    "idempotency_key": "restore-transfer-1",
                },
            )
            assert restored.status_code == 200, restored.text
            restored_case = restored.json()["body"]
            assert restored_case["version"] == 4
            assert (
                restored_case["history"][-1]["reverses_history_id"]
                == restored_case["history"][-2]["id"]
            )
            assert client.get("/paam/ledger/v1/entry/list").json()["total"] == 1
    finally:
        target_intake_preview_store.clear()
        engine.dispose()
