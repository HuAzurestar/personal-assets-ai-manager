from datetime import datetime
import shutil
import subprocess

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, Bill, BillTag, ImportBatch, LedgerOrigin, ReviewCandidate, Tag, TagAudit
from app.main import app, get_db


def test_health_and_bill_flow(tmp_path, monkeypatch):
    # Application setup uses a persistent DB in normal operation; this baseline
    # test validates the public health surface independently of user data.
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/health").json()["service"] == "personal-assets-ai-manager"
        created = client.post("/api/bills", json={"occurred_at": "2026-08-25T10:00:00", "merchant": "滴滴出行", "amount": -18.5, "note": "通勤"})
        assert created.status_code == 201
        assert created.json()["category"] == "交通出行"
        assert client.get("/api/dashboard").status_code == 200


def test_import_tag_audit_and_transfer_review(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}", connect_args={"check_same_thread": False})
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
            csv_body = "交易时间,交易对方,商品,收/支,金额(元),交易单号,支付方式\n2026-08-25 12:00:00,测试商户,午餐,支出,20.00,wx-001,微信零钱\n".encode()
            imported = client.post("/api/imports/wechat?filename=wechat.csv", content=csv_body)
            assert imported.status_code == 201
            assert imported.json()["imported_count"] == 1
            bill = next(item for item in client.get("/api/bills").json() if item["source_reference"] == "wx-001")
            assert bill["account_name"] == "微信零钱"
            manual = client.post(f"/api/bills/{bill['id']}/tags", json={"strategy": "manual", "category": "餐饮", "tags": ["消费", "午餐"]})
            assert manual.status_code == 201
            assert manual.json()["confidence"] == 0.95
            assert manual.json()["provider"] == "manual"
            history = client.get(f"/api/bills/{bill['id']}/tags").json()
            assert len(history) == 2
            with session_factory() as db:
                assert {"消费", "午餐"}.issubset({tag.name for tag in db.query(Tag).all()})
                assert db.query(BillTag).filter_by(bill_id=bill["id"]).count() == 2

            first = client.post("/api/bills", json={"occurred_at": "2026-08-25T13:00:00", "merchant": "账户转出", "account_name": "招商银行", "amount": -100, "note": "测试转账"})
            second = client.post("/api/bills", json={"occurred_at": "2026-08-25T13:01:00", "merchant": "账户转入", "account_name": "微信零钱", "amount": 100, "note": "测试转账"})
            assert first.status_code == second.status_code == 201
            candidates = client.get("/api/candidates").json()
            transfer = next(item for item in candidates if item["candidate_type"] == "transfer" and item["status"] == "pending")
            assert transfer["bill"]["account_name"] == "微信零钱"
            assert transfer["related_bill"]["account_name"] == "招商银行"
            assert transfer["bill"]["direction"] != transfer["related_bill"]["direction"]
            decided = client.post(f"/api/candidates/{transfer['id']}", json={"action": "confirm_transfer"})
            assert decided.status_code == 200
            assert decided.json()["status"] == "personal_transfer_grouped"
            assert decided.json()["transfer_group_id"]
            assert "不计入收入/支出汇总" in decided.json()["aggregation_effect"]
            dashboard = client.get("/api/dashboard").json()
            assert dashboard["income"] == 0
            assert dashboard["spending"] == -20
    finally:
        app.dependency_overrides.clear()


def test_duplicate_resolution_ignore_and_deferred_preserve_ledger_facts(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}", connect_args={"check_same_thread": False})
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
            def create_bill(at, merchant, amount):
                response = client.post("/api/bills", json={"occurred_at": at, "merchant": merchant, "account_name": "微信零钱", "amount": amount})
                assert response.status_code == 201
                return response.json()

            first = create_bill("2026-08-25T10:00:00", "同一商户", -10)
            second = create_bill("2026-08-25T10:01:00", "同一商户", -10)
            duplicate = next(item for item in client.get("/api/candidates").json() if item["candidate_type"] == "duplicate" and item["status"] == "pending")
            missing_choice = client.post(f"/api/candidates/{duplicate['id']}", json={"action": "resolve_duplicate"})
            assert missing_choice.status_code == 422
            resolved = client.post(f"/api/candidates/{duplicate['id']}", json={"action": "resolve_duplicate", "retained_bill_id": second["id"]})
            assert resolved.status_code == 200
            assert resolved.json()["status"] == "duplicate_excluded"
            assert resolved.json()["retained_bill_id"] == second["id"]
            bills = {item["id"]: item for item in client.get("/api/bills").json()}
            assert bills[first["id"]]["aggregate_excluded"] is True
            assert bills[first["id"]]["duplicate_of_id"] == second["id"]
            assert bills[second["id"]]["aggregate_excluded"] is False
            assert client.get("/api/dashboard").json()["spending"] == -10

            create_bill("2026-08-25T11:00:00", "忽略商户", -8)
            create_bill("2026-08-25T11:01:00", "忽略商户", -8)
            ignored = next(item for item in client.get("/api/candidates").json() if item["candidate_type"] == "duplicate" and item["status"] == "pending")
            assert client.post(f"/api/candidates/{ignored['id']}", json={"action": "ignored"}).json()["status"] == "ignored"

            create_bill("2026-08-25T12:00:00", "稍后商户", -5)
            create_bill("2026-08-25T12:01:00", "稍后商户", -5)
            deferred = next(item for item in client.get("/api/candidates").json() if item["candidate_type"] == "duplicate" and item["status"] == "pending")
            assert client.post(f"/api/candidates/{deferred['id']}", json={"action": "deferred"}).json()["status"] == "deferred"
            assert client.get("/api/dashboard").json()["spending"] == -36

            without_account = client.post("/api/bills", json={"occurred_at": "2026-08-25T13:00:00", "merchant": "普通商户 A", "amount": -30})
            other_without_account = client.post("/api/bills", json={"occurred_at": "2026-08-25T13:01:00", "merchant": "普通商户 B", "amount": 30})
            assert without_account.status_code == other_without_account.status_code == 201
            evidence_limited = next(item for item in client.get("/api/candidates").json() if item["candidate_type"] == "transfer" and {item["bill"]["merchant"], item["related_bill"]["merchant"]} == {"普通商户 A", "普通商户 B"})
            assert evidence_limited["status"] == "evidence_insufficient"
            assert "不能自动认定" in evidence_limited["reason"]
    finally:
        app.dependency_overrides.clear()


def test_tagging_strategies_are_distinct_and_auditable(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}", connect_args={"check_same_thread": False})
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
            created = client.post("/api/bills", json={"occurred_at": "2026-08-25T10:00:00", "merchant": "滴滴出行", "amount": -18.5, "note": "通勤"})
            assert created.status_code == 201
            bill_id = created.json()["id"]
            local = client.post(f"/api/bills/{bill_id}/tags", json={"strategy": "local_rules"})
            assert local.status_code == 201
            assert local.json()["provider"] == "local-rules"
            assert local.json()["confidence"] == 0.45
            llm = client.post(f"/api/bills/{bill_id}/tags", json={"strategy": "llm_suggestion"})
            assert llm.status_code == 201
            assert llm.json()["provider"] == "mock-rules"
            manual = client.post(f"/api/bills/{bill_id}/tags", json={"strategy": "manual", "category": "出行", "tags": ["人工", "通勤"]})
            assert manual.json()["provider"] == "manual"
            authorised = client.post(f"/api/bills/{bill_id}/tags", json={"strategy": "authorised_auto"})
            assert authorised.status_code == 201
            assert authorised.json()["confidence"] == 1.0
            history = client.get(f"/api/bills/{bill_id}/tags").json()
            assert {item["strategy"] for item in history} == {"local_rules", "llm_suggestion", "manual", "authorised_auto"}
            with session_factory() as db:
                assert db.query(TagAudit).filter_by(bill_id=bill_id, superseded=False).count() == 1
                assert db.query(BillTag).filter_by(bill_id=bill_id).count() > 0
    finally:
        app.dependency_overrides.clear()


def test_provider_icon_assets_are_local_and_referenced():
    with TestClient(app) as client:
        for path in ("/static/providers/alipay.svg", "/static/providers/wechat.svg"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("image/svg+xml")
        workbench = client.get("/static/workbench.js").text
        assert 'src="/static/providers/alipay.svg"' in workbench
        assert 'src="/static/providers/wechat.svg"' in workbench
        assert 'aria-label="预览并导入支付宝账单"' in workbench
        assert 'aria-label="预览并导入微信账单"' in workbench
        assert 'data-tag="authorised_auto"' in workbench
        assert '授权自动 1.00' in workbench
        assert '确认转移组' in workbench
        assert '保留流水 A' in workbench


def test_server_pagination_stable_sort_and_view_scoped_tags(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    with session_factory() as db:
        for number in range(125):
            db.add(Bill(occurred_at=datetime(2026, 8, 25, 10, 0), merchant=f"稳定排序-{number}", note="", amount=-10, account_name="测试账户", category="未分类", tags=""))
        db.commit()
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            first = client.get("/api/transactions?page=1&page_size=50&sort_by=occurred_at&sort_order=desc")
            second = client.get("/api/transactions?page=2&page_size=50&sort_by=occurred_at&sort_order=desc")
            third = client.get("/api/transactions?page=3&page_size=50&sort_by=occurred_at&sort_order=desc")
            assert [response.status_code for response in (first, second, third)] == [200, 200, 200]
            assert [first.json()["total"], second.json()["total"], third.json()["total"]] == [125, 125, 125]
            pages = [[item["id"] for item in response.json()["items"]] for response in (first, second, third)]
            assert [len(page) for page in pages] == [50, 50, 25]
            assert len(set().union(*map(set, pages))) == 125
            assert pages[0] == sorted(pages[0], reverse=True)

            created = client.post("/api/bills", json={"occurred_at": "2026-08-26T10:00:00", "merchant": "标签测试", "account_name": "测试账户", "amount": -20}).json()
            view = client.post("/api/tag-views", json={"name": "测试视图"}).json()
            unclassified = next(tag for tag in view["tags"] if tag["is_unclassified"])
            first_tag = client.post(f"/api/tag-views/{view['id']}/tags", json={"name": "餐饮"}).json()
            second_tag = client.post(f"/api/tag-views/{view['id']}/tags", json={"name": "交通"}).json()
            assert client.put(f"/api/transactions/{created['id']}/tag-assignments/{view['id']}", json={"tag_id": first_tag["id"]}).status_code == 200
            assigned = client.put(f"/api/transactions/{created['id']}/tag-assignments/{view['id']}", json={"tag_id": second_tag["id"]}).json()
            assert [(tag["view_id"], tag["tag_id"]) for tag in assigned["view_tags"]] == [(view["id"], second_tag["id"])]
            assert client.get(f"/api/transactions?tag={view['id']}:{second_tag['id']}").json()["total"] == 1
            assert client.get(f"/api/transactions?tag={view['id']}:{first_tag['id']}&tag={view['id']}:{second_tag['id']}").status_code == 400
            unclassified_result = client.get(f"/api/transactions?tag={view['id']}:{unclassified['id']}").json()
            assert unclassified_result["total"] == 125
            dashboard = client.get("/api/dashboard").json()
            assert "import_count" in dashboard and "trend" in dashboard
    finally:
        app.dependency_overrides.clear()


def test_workspace_and_update_script_are_present_and_safe():
    with TestClient(app) as client:
        workspace = client.get("/static/workspace-next.js").text
        assert 'data-page="summary"' in workspace
        assert 'data-page="data"' in workspace
        assert 'data-page="tags"' in workspace
        assert 'data-page="candidates"' in workspace
        assert 'aria-label="重复与转移候选"' in workspace
        assert 'aria-hidden="true" data-ascii-fallback="[SUM]">[ ≡ ]' in workspace
        assert 'aria-hidden="true" data-ascii-fallback="[IMP]">[ ↓ ]' in workspace
        assert 'aria-hidden="true" data-ascii-fallback="[TAG]">[ ¤ ]' in workspace
        assert 'aria-hidden="true" data-ascii-fallback="[ERR]">[ ! ]' in workspace
        assert "🏷" not in workspace and "↺" not in workspace
        assert 'data-candidate-undo' in workspace
        assert 'data-candidate-detail' in workspace
        assert 'confirm_personal_transfer' in workspace
        assert 'confirm_third_party_transfer' in workspace
        assert '批量个人转移' in workspace
        assert '批量他人转移' in workspace
        assert 'legacy_duplicate_needs_review' in workspace
        assert 'candidate-detail-dialog' in workspace
        assert 'data-batch="ignored"' in workspace
        assert "/api/transactions" in workspace
        css = client.get("/static/workspace.css").text
        assert '.workspace[data-collapsed="true"]' in css
        assert '.workspace[data-collapsed="true"] .sidebar nav button .nav-icon' in css
        assert "@media (max-width: 700px)" in css
        assert ".candidate-detail-dialog" in css
    script = open("scripts/update-and-run.ps1", encoding="utf-8").read()
    assert "git fetch origin main" in script
    assert "git merge --ff-only origin/main" in script
    assert "git status --porcelain" in script
    assert "git reset" not in script


def test_development_update_script_is_safe_parseable_and_does_not_build():
    script_path = "scripts/update-and-run-dev.ps1"
    script = open(script_path, encoding="utf-8").read()
    assert "git status --porcelain" in script
    assert "git fetch origin main" in script
    assert "git merge --ff-only origin/main" in script
    assert "Local changes detected" in script
    assert "& powershell -NoProfile -ExecutionPolicy Bypass -File" in script
    assert "& $venvPython -m pytest -q" in script
    assert '& $venvPython (Join-Path $projectRoot "run.py")' in script
    assert "pyinstaller" not in script.lower()
    assert "build.py" not in script
    assert "git reset" not in script

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    assert powershell, "PowerShell is required for the Windows development script"
    command = "& { $tokens = $null; $errors = $null; [System.Management.Automation.Language.Parser]::ParseFile('scripts/update-and-run-dev.ps1', [ref]$tokens, [ref]$errors) | Out-Null; if ($errors.Count) { $errors | ForEach-Object { Write-Error $_ }; exit 1 } }"
    parsed = subprocess.run([powershell, "-NoProfile", "-Command", command], capture_output=True, text=True, check=False)
    assert parsed.returncode == 0, parsed.stderr


def test_candidate_page_batch_and_undo_restore_ledger_facts(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}", connect_args={"check_same_thread": False})
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
            def create_bill(at, merchant, amount):
                response = client.post("/api/bills", json={"occurred_at": at, "merchant": merchant, "account_name": "test-account", "amount": amount})
                assert response.status_code == 201
                return response.json()

            first = create_bill("2026-08-25T10:00:00", "same merchant", -12)
            second = create_bill("2026-08-25T10:01:00", "same merchant", -12)
            duplicate = next(item for item in client.get("/api/candidates").json() if item["candidate_type"] == "duplicate")
            page = client.get("/api/candidates/page?page=1&page_size=20&status=pending&candidate_type=duplicate")
            assert page.status_code == 200
            assert page.json()["total"] == 1
            assert page.json()["items"][0]["id"] == duplicate["id"]

            batch = client.post("/api/candidates/batch", json={"items": [{"candidate_id": duplicate["id"], "action": "resolve_duplicate", "retained_bill_id": second["id"]}]})
            assert batch.status_code == 200
            resolved = batch.json()[0]
            assert resolved["status"] == "duplicate_excluded"
            assert resolved["undo_available"] is True
            assert client.get("/api/dashboard").json()["spending"] == -12
            audit = client.get(f"/api/candidates/{duplicate['id']}/actions").json()
            assert audit[0]["action"] == "resolve_duplicate" and audit[0]["undone"] is False

            undone = client.post(f"/api/candidates/{duplicate['id']}/undo")
            assert undone.status_code == 200
            assert undone.json()["status"] == "pending"
            assert undone.json()["undo_available"] is False
            bills = {bill["id"]: bill for bill in client.get("/api/bills").json()}
            assert bills[first["id"]]["aggregate_excluded"] is False
            assert bills[first["id"]]["duplicate_of_id"] is None
            assert client.get("/api/dashboard").json()["spending"] == -24
            audit = client.get(f"/api/candidates/{duplicate['id']}/actions").json()
            assert audit[0]["undone"] is True

            deferred = client.post("/api/candidates/batch", json={"items": [{"candidate_id": duplicate["id"], "action": "deferred"}]})
            assert deferred.status_code == 200
            assert deferred.json()[0]["status"] == "deferred"
    finally:
        app.dependency_overrides.clear()


def test_candidate_detail_legacy_duplicate_and_transfer_tracking(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}", connect_args={"check_same_thread": False})
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
            def create_bill(at, merchant, amount, account="account-a"):
                response = client.post("/api/bills", json={"occurred_at": at, "merchant": merchant, "account_name": account, "amount": amount, "note": "fixture"})
                assert response.status_code == 201
                return response.json()

            first = create_bill("2026-08-25T09:00:00", "duplicate fixture", -10)
            second = create_bill("2026-08-25T09:01:00", "duplicate fixture", -10)
            duplicate = next(item for item in client.get("/api/candidates").json() if item["candidate_type"] == "duplicate")
            with session_factory() as db:
                batch = ImportBatch(source_type="wechat", filename="fixture.csv", imported_at=datetime(2026, 8, 25, 9), row_count=2, imported_count=2)
                db.add(batch)
                db.flush()
                db.add_all([
                    LedgerOrigin(bill_id=first["id"], source_type="wechat", source_reference="serial-a", raw_payload='{"field":"a"}', import_batch_id=batch.id),
                    LedgerOrigin(bill_id=second["id"], source_type="wechat", source_reference="serial-b", raw_payload='{"field":"b"}', import_batch_id=batch.id),
                ])
                db.get(ReviewCandidate, duplicate["id"]).status = "legacy_duplicate_needs_review"
                db.commit()

            detail = client.get(f"/api/candidates/{duplicate['id']}/detail")
            assert detail.status_code == 200
            detail_body = detail.json()
            assert {detail_body["first"]["source"]["source_reference"], detail_body["second"]["source"]["source_reference"]} == {"serial-a", "serial-b"}
            assert {detail_body["first"]["source"]["batch_filename"], detail_body["second"]["source"]["batch_filename"]} == {"fixture.csv"}
            assert {detail_body["first"]["raw_fields"]["field"], detail_body["second"]["raw_fields"]["field"]} == {"a", "b"}
            resolved = client.post(f"/api/candidates/{duplicate['id']}", json={"action": "resolve_duplicate", "retained_bill_id": second["id"]})
            assert resolved.status_code == 200 and resolved.json()["status"] == "duplicate_excluded"
            assert client.get("/api/dashboard").json()["spending"] == -10
            assert client.post(f"/api/candidates/{duplicate['id']}/undo").json()["status"] == "legacy_duplicate_needs_review"
            assert client.get("/api/dashboard").json()["spending"] == -20

            outbound = create_bill("2026-08-25T10:00:00", "internal out", -100, "bank-a")
            inbound = create_bill("2026-08-25T10:01:00", "internal in", 100, "wallet-b")
            personal = next(item for item in client.get("/api/candidates").json() if item["candidate_type"] == "transfer" and {item["bill"]["id"], item["related_bill"]["id"]} == {outbound["id"], inbound["id"]})
            personal_result = client.post(f"/api/candidates/{personal['id']}", json={"action": "confirm_personal_transfer"})
            assert personal_result.status_code == 200
            assert personal_result.json()["status"] == "personal_transfer_grouped"
            assert personal_result.json()["transfer_kind"] == "personal"
            personal_bills = {bill["id"]: bill for bill in client.get("/api/bills").json()}
            assert personal_bills[outbound["id"]]["aggregate_excluded"] and personal_bills[inbound["id"]]["aggregate_excluded"]
            assert client.get("/api/dashboard").json()["spending"] == -20
            assert client.post(f"/api/candidates/{personal['id']}/undo").json()["status"] == "pending"
            assert client.get("/api/dashboard").json()["spending"] == -120
            assert client.post(f"/api/candidates/{personal['id']}", json={"action": "confirm_personal_transfer"}).status_code == 200

            external_out = create_bill("2026-08-25T11:00:00", "agent payment", -40, "未提供账户")
            external_in = create_bill("2026-08-25T11:01:00", "agent collection", 40, "未提供账户")
            external = next(item for item in client.get("/api/candidates").json() if item["candidate_type"] == "transfer" and {item["bill"]["id"], item["related_bill"]["id"]} == {external_out["id"], external_in["id"]})
            assert external["status"] == "evidence_insufficient"
            third_party = client.post(f"/api/candidates/{external['id']}", json={"action": "confirm_third_party_transfer"})
            assert third_party.status_code == 200
            assert third_party.json()["status"] == "third_party_transfer_grouped"
            assert third_party.json()["transfer_kind"] == "third_party"
            third_party_bills = {bill["id"]: bill for bill in client.get("/api/bills").json()}
            assert third_party_bills[external_out["id"]]["aggregate_excluded"] and third_party_bills[external_in["id"]]["aggregate_excluded"]
            assert client.get("/api/dashboard").json()["spending"] == -20
            assert client.post(f"/api/candidates/{external['id']}/undo").json()["status"] == "evidence_insufficient"
            assert client.get("/api/dashboard").json()["spending"] == -60
    finally:
        app.dependency_overrides.clear()
