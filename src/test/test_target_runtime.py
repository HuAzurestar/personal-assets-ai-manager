import base64
import csv
import io
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from backend.core import target_database
from backend import target_main
from backend.core.intake_preview_store import target_intake_preview_store


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
        "/paam/ledger/v1/review/{review_id}/confirm",
        "/paam/ledger/v1/review/{review_id}/revoke",
        "/paam/ledger/v1/review/{review_id}/restore",
        "/paam/ledger/v1/review_candidate/list",
        "/paam/ledger/v1/flow/{ledger_id}/account",
    } <= paths
    assert not any(path.startswith("/paam/economy/") for path in paths)
    assert not any(path.startswith("/paam/review/v2") for path in paths)
    assert {
        "/paam/import/v1/fact_conflict/list",
        "/paam/import/v1/fact_conflict/{conflict_id}",
        "/paam/import/v1/fact_conflict/{conflict_id}/resolve",
        "/paam/import/v1/fact_conflict/{conflict_id}/dismiss",
        "/paam/import/v1/fact_conflict/{conflict_id}/reopen",
        "/paam/import/v1/import_file/summary",
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
        "LedgerEntryPageResponse",
        "LedgerEntryDetailResponse",
        "LedgerEntrySummaryResponse",
        "TargetEconomicReviewResponse",
        "TargetEconomicReviewPageResponse",
        "ImportFactConflictResponse",
        "ImportFactConflictPageResponse",
        "ImportFileSummaryResponse",
        "LedgerAccountResponse",
        "TargetReviewCandidatePageResponse",
    ):
        assert schemas[name]["properties"]["status"]["const"] == 200

    entry_properties = set(schemas["LedgerEntryListItem"]["properties"])
    assert {"entry_type", "entry_direction", "amount", "currency_code"} <= entry_properties
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

    review_request_properties = set(
        schemas["TargetEconomicReviewCreateRequest"]["properties"]
    )
    assert {"title", "economics", "allocations"} <= review_request_properties
    assert {"description", "entries"}.isdisjoint(review_request_properties)
    economic_request_properties = set(
        schemas["TargetEconomicDefinitionRequest"]["properties"]
    )
    assert "economic_type" in economic_request_properties
    assert "entry_type" not in economic_request_properties
    review_properties = set(schemas["TargetEconomicReviewRead"]["properties"])
    assert {"title", "economics"} <= review_properties
    assert {"description", "ledger_entries"}.isdisjoint(review_properties)
    review_allocation_properties = set(
        schemas["TargetFlowAllocationRead"]["properties"]
    )
    assert {"fact_id", "economic_id"} <= review_allocation_properties
    assert {"transaction_fact_id", "ledger_entry_id"}.isdisjoint(
        review_allocation_properties
    )

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


def test_sample_import_script_queries_the_canonical_flow_api():
    script = (ROOT / "src/script/import_test_sample.py").read_text(encoding="utf-8")
    assert "/paam/ledger/v1/flow/list" in script
    assert "/paam/ledger/v1/entry/" not in script


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
            assert "/paam/import/v1/preview/" in script.text
            assert "/paam/ledger/v1/flow" in script.text
            assert "/paam/ledger/v1/review" in script.text
            assert "/paam/ledger/v1/review_candidate" in script.text
            assert "/paam/ledger/v1/fact/list" not in script.text
            assert "/paam/review/v1/account" not in script.text
            assert "/paam/review/v2" not in script.text
            assert "/paam/ledger/v1/entry/" not in script.text
            core_script = client.get("/static/js/util/core.js")
            assert core_script.status_code == 200
            assert "export const reviewTypeNames" in core_script.text
            assert "export const roleNames" in core_script.text
            for path in (
                "/static/js/util/core.js", "/static/js/navigation.js",
                "/static/js/view/account.js", "/static/js/api/client.js",
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
            confirmation = client.post(
                    f"/paam/import/v1/preview/{preview['token']}/confirm",
                json={"version": preview["version"]},
            )
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
            confirmation = client.post(
                    f"/paam/import/v1/preview/{preview['token']}/confirm",
                json={"version": preview["version"]},
            )
            assert confirmation.status_code == 200, confirmation.text
            fact_ids = confirmation.json()["body"]["bill_fact_ids"]
            assert len(fact_ids) == 2

            created = client.post(
                "/paam/ledger/v1/review",
                json={
                    "behavior_code": "TRANSFER",
                    "title": "零钱转入招行",
                    "economics": [
                        {"client_key": "out", "economic_type": "ACCOUNT_TRANSFER"},
                        {"client_key": "in", "economic_type": "ACCOUNT_TRANSFER"},
                    ],
                    "allocations": [
                        {
                            "fact_id": fact_ids[0],
                            "economic_key": "out",
                            "amount": 1000,
                        },
                        {
                            "fact_id": fact_ids[1],
                            "economic_key": "in",
                            "amount": 999,
                        },
                    ],
                    "reason": "识别本人账户转账",
                    "idempotency_key": "create-transfer-1",
                },
            )
            assert created.status_code == 200, created.text
            case = created.json()["body"]
            assert case["status"] == "PENDING"
            assert case["version"] == 1
            assert [row["amount"] for row in case["allocations"]] == [1000, 999]
            assert case["history"][0]["operation"] == "CREATE"

            confirmed = client.post(
                f"/paam/ledger/v1/review/{case['id']}/confirm",
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
            page = client.get("/paam/ledger/v1/flow/list").json()["body"]
            assert page["total"] == 2
            assert {
                (item["entry_type"], item["entry_direction"], item["amount"])
                for item in page["items"]
            } == {
                (1, 1, 999),
                (1, 2, 1000),
            }
            detail = client.get(
                f"/paam/ledger/v1/flow/{page['items'][0]['id']}"
            ).json()["body"]
            assert len(detail["facts"]) == 1
            assert len(detail["allocations"]) == 1
            assert detail["reviews"][0]["id"] == case["id"]
            assert detail["reviews"][0]["behavior_type"] == 0
            summary = client.get("/paam/ledger/v1/flow/summary").json()["body"]
            assert summary["totals"][0]["internal_transfer_in_amount"] == 999
            assert summary["totals"][0]["internal_transfer_out_amount"] == 1000

            replay = client.post(
                f"/paam/ledger/v1/review/{case['id']}/confirm",
                json={
                    "expected_version": 1,
                    "reason": "确认本人转账",
                    "idempotency_key": "confirm-transfer-1",
                },
            )
            assert replay.status_code == 200
            assert replay.json() == confirmed.json()

            revoked = client.post(
                f"/paam/ledger/v1/review/{case['id']}/revoke",
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
            assert case["history"][-1]["operation"] == "REVOKE"
            page = client.get("/paam/ledger/v1/flow/list").json()["body"]
            assert page["total"] == 2
            assert {item["entry_type"] for item in page["items"]} == {0}

            restored = client.post(
                f"/paam/ledger/v1/review/{case['id']}/restore",
                json={
                    "expected_version": 3,
                    "reason": "恢复核查",
                    "idempotency_key": "restore-transfer-1",
                },
            )
            assert restored.status_code == 200, restored.text
            restored_case = restored.json()["body"]
            assert restored_case["version"] == 4
            assert restored_case["history"][-1]["operation"] == "RESTORE"
            assert client.get("/paam/ledger/v1/flow/list").json()["body"]["total"] == 2
    finally:
        target_intake_preview_store.clear()
        engine.dispose()
