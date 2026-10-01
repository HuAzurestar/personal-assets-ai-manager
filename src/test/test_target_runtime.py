import base64
import csv
import io
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from backend import target_main
from backend.core import target_database
from backend.core.import_preview_store import import_preview_store as target_intake_preview_store
from import_batch_helpers import confirm_api_batch

ROOT = Path(__file__).resolve().parents[2]


def test_openapi_locks_canonical_ledger_v1_contract():
    specification = target_main.app.openapi()
    paths = set(specification["paths"])
    assert {
        "/paam/ledger/v1/flow/list",
        "/paam/ledger/v1/flow/summary",
        "/paam/ledger/v1/flow/{ledger_id}",
        "/paam/ledger/v1/review",
        "/paam/ledger/v1/review/list",
        "/paam/ledger/v1/review/{review_id}",
        "/paam/ledger/v1/review/{review_id}/revoke",
        "/paam/ledger/v1/review/{review_id}/restore",
        "/paam/ledger/v1/review_candidate/list",
        "/paam/ledger/v1/flow/{ledger_id}/account",
    } <= paths
    assert "/paam/ledger/v1/review/{review_id}/confirm" not in paths
    assert not any(path.startswith("/paam/economy/") for path in paths)
    assert not any(path.startswith("/paam/review/v2") for path in paths)
    assert {
        "/paam/import/v1/fact_conflict/list",
        "/paam/import/v1/fact_conflict/{conflict_id}",
        "/paam/import/v1/fact_conflict/{conflict_id}/resolve",
        "/paam/import/v1/fact_conflict/{conflict_id}/dismiss",
        "/paam/import/v1/fact_conflict/{conflict_id}/reopen",
        "/paam/import/v1/import_file/summary",
        "/paam/import/v1/import_file/{import_file_id}/row/list",
    } <= paths
    assert "/paam/review/v1/case/page" not in paths
    assert "/paam/review/v1/case/detail/{case_id}" not in paths
    assert not any("fact-conflict" in path for path in paths)
    assert "/paam/import/v1/batch/list" not in paths
    assert "/paam/import/v1/batch/{batch_id}/row/list" not in paths
    assert "/paam/import/v1/account/list" not in paths
    assert "/paam/ledger/v1/entry/list" not in paths
    assert "/paam/review/v1/case/create" not in paths
    assert "/paam/ledger/v1/fact/list" not in paths
    assert "/paam/review/v1/account/set/{fact_id}" not in paths
    assert "/paam/review/v1/account/revoke/{case_id}" not in paths
    assert "/paam/review/v1/account/restore/{case_id}" not in paths

    schemas = specification["components"]["schemas"]
    for name in (
        "LedgerEntryListResponse",
        "LedgerEntryDetailResponse",
        "LedgerEntrySummaryResponse",
        "ReviewReadResponse",
        "ReviewPreviewResponse",
        "ReviewCommandResponse",
        "ReviewCaseListResponse",
        "ImportPreviewResponse",
        "ImportConfirmResponse",
        "SourceRowDetailResponse",
        "ImportFileSummaryResponse",
        "LedgerAccountResponse",
        "TargetReviewCandidateListResponse",
    ):
        assert schemas[name]["properties"]["status"]["const"] == 200

    entry_properties = set(schemas["LedgerEntryListItem"]["properties"])
    assert {
        "active", "entry_type", "entry_direction", "amount", "currency_code"
    } <= entry_properties
    assert {"economic_type", "cash_direction", "projection_version"}.isdisjoint(entry_properties)
    detail_properties = set(schemas["LedgerEntryDetailRead"]["properties"])
    assert "ledger_entry" in detail_properties
    assert "flow" not in detail_properties
    allocation_properties = set(schemas["LedgerAllocationEvidenceRead"]["properties"])
    assert {
        "review_case_id",
        "transaction_fact_id",
        "ledger_entry_id",
    } <= allocation_properties
    assert {"review_id", "fact_id", "economic_id"}.isdisjoint(allocation_properties)

    review_request_properties = set(schemas["ReviewChangeInput"]["properties"])
    assert {"deactivate_review_ids","activate_review_ids","new_reviews","expected_reviews"} == review_request_properties
    assert set(schemas["MoneySplitInput"]["properties"]) == {"transaction_id","economic_type","cash_amount","account_ref_id"}
    assert {"history","version","behavior_type"}.isdisjoint(schemas["ReviewReadPO"]["properties"])
    assert {"review_id","transaction_id","ledger_id","cash_amount","cash_currency_code"} <= set(schemas["FirstAllocationPO"]["properties"])
    assert "/paam/ledger/v1/review/preview" in paths
    assert "/paam/ledger/v1/review/command" in paths
    assert "active" in schemas["TransactionFactLedgerRead"]["properties"]

    for path in ("/paam/ledger/v1/flow/list", "/paam/ledger/v1/review/list"):
        parameters = specification["paths"][path]["get"]["parameters"]
        page_size = next(item for item in parameters if item["name"] == "page_size")
        assert page_size["schema"]["default"] == 20

    assert specification["paths"]["/paam/ledger/v1/flow/list"]["get"]["tags"] == [
        "ledger-flow"
    ]
    assert specification["paths"]["/paam/ledger/v1/review"]["post"]["tags"] == [
        "ledger-review"
    ]


def test_importing_target_runtime_does_not_load_legacy_database_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.path.insert(0, 'src'); import backend.target_main; "
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
    assert 'uvicorn.run("backend.target_main:app"' in (ROOT / "run.py").read_text(
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
            assert client.get("/demo").status_code == 404
            assert client.get("/static/layered-demo.js").status_code == 404
            assert client.get("/static/layered-demo.css").status_code == 404
            script = client.get("/static/target-ledger.js")
            assert script.status_code == 200
            assert './js/view/ledger.js' in script.text
            script = client.get("/static/js/view/ledger.js")
            assert script.status_code == 200
            assert "/paam/ledger/v1/flow" in script.text
            assert "ledger_type" not in script.text
            assert "mountImportBatch" in script.text
            import_script = client.get("/static/js/view/import-batch.js")
            assert import_script.status_code == 200
            assert "/paam/import/v1/preview/" in import_script.text
            assert "expected_updated_time" in import_script.text
            assert "selected_rows" in import_script.text
            assert "/paam/ledger/v1/flow" in script.text
            assert "/paam/ledger/v1/review" in script.text
            assert "mountReviewWorkbench" in script.text
            assert "/paam/ledger/v1/review_candidate" not in script.text
            review_script = client.get("/static/js/view/review-workbench.js")
            assert review_script.status_code == 200
            assert "/paam/ledger/v1/candidate" in review_script.text
            assert '`${base}/preview`' in review_script.text
            assert '`${base}/command`' in review_script.text
            assert "idempotency_key" not in review_script.text
            position_script = client.get("/static/js/view/position.js")
            assert position_script.status_code == 200
            assert "/paam/financial/v1/position" in position_script.text
            assert "UNKNOWN" in position_script.text
            assert "NEEDS_REVIEW" in position_script.text
            assert "/paam/ledger/v1/fact/list" not in script.text
            assert "/paam/review/v1/account" not in script.text
            assert "/paam/review/v2" not in script.text
            assert "/paam/ledger/v1/entry/" not in script.text
            assert "automationSettingsPage" in script.text
            automation_script = client.get("/static/js/view/automation.js")
            assert automation_script.status_code == 200
            assert "/paam/system/v1/setting/automation" in automation_script.text
            assert "/paam/tag/v1/auto_rule" in automation_script.text
            assert "/paam/tag/v1/assignment_request" in automation_script.text
            assert "/paam/system/v1/schedule/status" in automation_script.text
            assert "没有已注册的自动标签任务" in automation_script.text
            assert 'data-auto-notice' in automation_script.text
            explanation_script = client.get("/static/js/view/automation_explain.js")
            assert explanation_script.status_code == 200
            assert "尚未调度" in explanation_script.text
            assert 'schedule.tag_scan_guard === "DISABLED"' in explanation_script.text
            assert "保存后按 CRON 执行" not in automation_script.text
            assert "启用后立即注册" not in automation_script.text
            assert "尚未启动调度" not in automation_script.text
            schedule = client.get("/paam/system/v1/schedule/status")
            assert schedule.status_code == 200
            assert schedule.json()["body"]["scheduler_state"] == "RUNNING"
            assert schedule.json()["body"]["tag_scan_guard"] == "REAL_READY"
            assert 'data-action="tag-request-batch"' in automation_script.text
            inspection_script = client.get("/static/js/component/inspection.js")
            assert inspection_script.status_code == 200
            assert "AUTO_RULE" in inspection_script.text
            assert "Request #" in inspection_script.text
            assert "不调用模型" in explanation_script.text
            assert "模型不可用" in explanation_script.text
            assert 'data-action="rule-run"' not in automation_script.text
            assert 'data-action="rule-rescan"' not in automation_script.text
            navigation_script = client.get("/static/js/navigation.js")
            assert navigation_script.status_code == 200
            assert 'page: "settings"' in navigation_script.text
            assert 'data-page="import">导入账单</button>' in navigation_script.text
            assert '["tag-review", "打标签审查"' in navigation_script.text
            assert '["auto-rules", "自动规则"' in navigation_script.text
            core_script = client.get("/static/js/util/core.js")
            assert core_script.status_code == 200
            assert "export const reviewTypeNames" in core_script.text
            assert "export const roleNames" in core_script.text
            for path in (
                "/static/js/util/core.js", "/static/js/navigation.js",
                "/static/js/view/account.js", "/static/js/api/client.js",
                "/static/js/view/automation.js",
                "/static/js/component/toast.js", "/static/js/component/table.js",
                "/static/js/state/ledger.js", "/static/js/theme.js",
                "/static/css/ledger.css", "/static/css/theme.css", "/static/css/target.css",
                "/asset/personal-assets-ai-manager.svg", "/asset/provider/wechat.svg",
                "/asset/provider/alipay.svg",
            ):
                asset = client.get(path)
                assert asset.status_code == 200, path
                if path == "/static/js/view/account.js":
                    assert "ledger_type" not in asset.text
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
            confirmation = confirm_api_batch(client, preview)
            assert confirmation.status_code == 200, confirmation.text
            page = client.get("/paam/ledger/v1/flow/list")
            assert page.status_code == 200, page.text
            assert page.json()["body"]["total"] == 1
            for legacy_path in (
                "/paam/ledger/v2/entry/list",
                "/paam/ledger/v2/entry/detail/1",
                "/paam/ledger/v2/summary",
            ):
                assert client.get(legacy_path).status_code == 404
            assert client.get("/paam/review/v1/case/list").status_code == 404
            assert client.post("/paam/review/v1/case/create", json={}).status_code == 404
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
            confirmation = confirm_api_batch(client, preview)
            assert confirmation.status_code == 200, confirmation.text
            fact_ids = [row["transaction_id"] for row in confirmation.json()["body"]["processed_rows"]]
            assert len(fact_ids) == 2

            def command(**intent):
                response = client.post("/paam/ledger/v1/review/preview", json=intent)
                assert response.status_code == 200, response.text
                plan = response.json()["body"]
                assert not plan["blocking_issues"], plan
                response = client.post("/paam/ledger/v1/review/command", json=intent | dict(
                    expected_reviews=plan["expected_reviews"], preview_digest=plan["preview_digest"]))
                assert response.status_code == 200, response.text
                return response.json()["body"]

            result = command(new_reviews=[dict(case_code="INTERNAL_TRANSFER", title="Mock transfer",
                parameters=dict(transaction_ids=fact_ids))])
            rid = result["created_reviews"][0]["id"]
            original = client.get(f"/paam/ledger/v1/review/{rid}").json()["body"]
            assert original["status"] == "CONFIRMED"
            assert original["type"] == "OTHER_MANUAL"
            assert [row["cash_amount"] for row in original["allocations"]] == [1000,999]
            assert "history" not in original
            assert all(row["economic_type"] == "ACCOUNT_TRANSFER" for row in original["ledger_entries"])
            summary = client.get("/paam/ledger/v1/flow/summary").json()["body"]["totals"][0]
            assert summary["internal_transfer_in_amount"] == 999
            assert summary["internal_transfer_out_amount"] == 1000
            command(deactivate_review_ids=[rid])
            revoked = client.get(f"/paam/ledger/v1/review/{rid}").json()["body"]
            assert revoked["status"] == "REVOKED"
            assert revoked["ledger_entries"] == original["ledger_entries"]
            assert client.get("/paam/ledger/v1/flow/list").json()["body"]["total"] == 4
            for fact_id in fact_ids:
                detail = client.get(f"/paam/ledger/v1/transaction_fact/{fact_id}").json()["body"]
                assert sum(row["active"] for row in detail["ledgers"]) == 1
            command(activate_review_ids=[rid])
            restored = client.get(f"/paam/ledger/v1/review/{rid}").json()["body"]
            assert restored["status"] == "CONFIRMED"
            assert restored["allocations"] == original["allocations"]
            assert restored["ledger_entries"] == original["ledger_entries"]
            assert client.get("/paam/ledger/v1/flow/list").json()["body"]["total"] == 4
    finally:
        target_intake_preview_store.clear()
        engine.dispose()
