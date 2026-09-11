import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import shutil
import subprocess
from time import perf_counter

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

import app.database as database
from app.database import Base, Bill, BillTag, ImportBatch, LedgerOrigin, ReviewCandidate, Tag, TagAudit, TagView, ViewTag
from app.main import app, get_db


def test_health_and_bill_flow(tmp_path, monkeypatch):
    # Never let this baseline smoke test write into the user's default ledger.
    import app.main as main
    engine = create_engine(f"sqlite:///{tmp_path / 'health.db'}", connect_args={"check_same_thread": False})
    sessions = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/health").json()["service"] == "personal-assets-ai-manager"
        created = client.post("/api/bills", json={"occurred_at": "2026-08-25T10:00:00", "merchant": "滴滴出行", "amount": -18.5, "note": "通勤"})
        assert created.status_code == 201
        assert created.json()["category"] == "未分类"
        assert client.get(f"/api/bills/{created.json()['id']}/tags").json()[0]["action"] == "suggest"
        assert client.get("/api/dashboard").status_code == 200
    engine.dispose()


def test_init_db_assigns_system_names_to_all_default_tags(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}", connect_args={"check_same_thread": False})
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", session_factory)

    database.init_db()

    with session_factory() as db:
        views = db.scalars(select(TagView).order_by(TagView.id)).all()
        assert [view.system_name for view in views] == ["category", "scenario"]
        for view in views:
            tags = db.scalars(select(ViewTag).where(ViewTag.view_id == view.id).order_by(ViewTag.id)).all()
            assert tags
            assert all(tag.system_name for tag in tags)
            assert sum(tag.is_unclassified for tag in tags) == 1


def _seed_json_tag_definitions(db):
    category = TagView(name="消费类别", system_name="category", created_at=datetime.now())
    scenario = TagView(name="使用场景", system_name="scenario", created_at=datetime.now())
    db.add_all([category, scenario])
    db.flush()
    db.add_all([
        ViewTag(view_id=category.id, name="未分类", system_name="unclassified", is_unclassified=True),
        ViewTag(view_id=category.id, name="餐饮", system_name="food"),
        ViewTag(view_id=category.id, name="交通", system_name="transport"),
        ViewTag(view_id=scenario.id, name="未分类", system_name="unclassified", is_unclassified=True),
        ViewTag(view_id=scenario.id, name="日常", system_name="daily"),
    ])
    db.commit()


def test_json_tag_state_migrates_legacy_labels_without_mutating_legacy_rows(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        _seed_json_tag_definitions(db)
        db.add(Bill(occurred_at=datetime(2026, 8, 1), merchant="旧账单", note="", amount=-10, category="餐饮", tags="旧标签"))
        db.commit()

    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", session_factory)
    monkeypatch.setattr(database, "DATABASE_URL", "sqlite:///migration.db")
    database.init_db()

    with session_factory() as db:
        bill = db.scalar(db.query(Bill).statement)
        assert json.loads(bill.tag_state_json) == {"category": "unclassified", "scenario": "unclassified"}
        assert bill.tags == "旧标签"
        assert db.query(BillTag).count() == 0
        audit = db.query(TagAudit).filter_by(bill_id=bill.id, provider="legacy_tag_state").one()
        assert json.loads(audit.tag_state_json) == {"category": "unclassified", "scenario": "unclassified"}


def test_json_tag_state_api_is_name_based_exclusive_and_indexed_at_scale(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'tag-state-scale.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with engine.begin() as connection:
        connection.execute(text("CREATE INDEX ix_bills_tag_state_category_page ON bills(json_extract(tag_state_json, '$.category'), occurred_at DESC, id DESC)"))
    with session_factory() as db:
        _seed_json_tag_definitions(db)
        base_time = datetime(2026, 8, 1)
        db.bulk_insert_mappings(Bill, [
            {
                "occurred_at": base_time,
                "merchant": f"构造流水-{index}",
                "note": "",
                "amount": -float(index % 19 + 1),
                "category": "旧分类",
                "tags": "旧标签",
                "account_name": "测试账户",
                "aggregate_excluded": False,
                "tag_state_json": json.dumps({"category": "food" if index % 2 else "transport", "scenario": "daily"}),
            }
            for index in range(20_000)
        ])
        db.commit()

    def override_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            selected = client.put("/api/transactions/2/tag-state", json={"tag_state": {"category": "food", "scenario": "daily"}})
            assert selected.status_code == 200
            assert selected.json()["tag_state"] == {"category": "food", "scenario": "daily"}
            assert {item["view_system_name"] for item in selected.json()["view_tags"]} == {"category", "scenario"}
            assert client.put("/api/transactions/2/tag-state", json={"tag_state": {"消费类别": "餐饮"}}).status_code == 422
            oversized = {f"v{index:02d}_{'x' * 58}": f"t{'y' * 62}" for index in range(30)}
            assert client.put("/api/transactions/2/tag-state", json={"tag_state": oversized}).status_code == 422
            assert client.put("/api/transactions/2/tag-state", content=b'{"tag_state": []}', headers={"Content-Type": "application/json"}).status_code == 422
            assert client.get("/api/transactions?tag=category:food&page=1&page_size=100").json()["total"] == 10_000
            start = perf_counter()
            page_one = client.get("/api/transactions?tag=category:food&page=1&page_size=100")
            page_two = client.get("/api/transactions?tag=category:food&page=2&page_size=100")
            assert perf_counter() - start < 2.5
            assert page_one.status_code == page_two.status_code == 200
            assert len({item["id"] for item in page_one.json()["items"] + page_two.json()["items"]}) == 200
    finally:
        app.dependency_overrides.clear()

    with engine.connect() as connection:
        plan = connection.execute(text("EXPLAIN QUERY PLAN SELECT id FROM bills WHERE json_extract(tag_state_json, '$.category') = 'food' ORDER BY occurred_at DESC, id DESC LIMIT 100")).all()
        assert "ix_bills_tag_state_category_page" in " ".join(str(row) for row in plan)


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
                assert db.query(Tag).count() == 0
                assert db.query(BillTag).filter_by(bill_id=bill["id"]).count() == 0

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
                assert db.query(BillTag).filter_by(bill_id=bill_id).count() == 0
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
        assert '/tag-state' in workspace
        assert 'data-tag-state' in workspace
        assert 'system_name' in workspace
        assert 'prompt(' not in workspace
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
        assert '不是重复（拒绝建议，全部计入）' in workspace
        assert '原始字段（默认展开）' in workspace
        assert '关闭候选详情，不改变候选或流水状态' in workspace
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

            batch = client.post("/api/candidates/batch", json={"items": [{"candidate_id": duplicate["id"], "action": "resolve_duplicate", "retained_bill_id": second["id"], "idempotency_key": "candidate-1"}]})
            assert batch.status_code == 200
            resolved = batch.json()[0]
            assert resolved["status"] == "duplicate_excluded"
            assert resolved["undo_available"] is True
            assert client.get("/api/dashboard").json()["spending"] == -12
            audit = client.get(f"/api/candidates/{duplicate['id']}/actions").json()
            assert audit[0]["action"] == "resolve_duplicate" and audit[0]["undone"] is False
            assert audit[0]["idempotency_key"] == "candidate-1"
            repeated = client.post(f"/api/candidates/{duplicate['id']}", json={"action": "resolve_duplicate", "retained_bill_id": second["id"], "idempotency_key": "candidate-1"})
            assert repeated.status_code == 200
            conflict = client.post(f"/api/candidates/{duplicate['id']}", json={"action": "reject_duplicate", "idempotency_key": "candidate-1"})
            assert conflict.status_code == 409
            assert len(client.get(f"/api/candidates/{duplicate['id']}/actions").json()) == 1

            undone = client.post(f"/api/candidates/{duplicate['id']}/undo")
            assert undone.status_code == 200
            assert undone.json()["status"] == "pending"
            assert undone.json()["undo_available"] is False
            bills = {bill["id"]: bill for bill in client.get("/api/bills").json()}
            assert bills[first["id"]]["aggregate_excluded"] is False
            assert bills[first["id"]]["duplicate_of_id"] is None
            assert client.get("/api/dashboard").json()["spending"] == -24
            audit = client.get(f"/api/candidates/{duplicate['id']}/actions").json()
            assert [item["action"] for item in audit[:2]] == ["undo", "resolve_duplicate"]
            assert audit[0]["actor"] == "local-user"
            assert audit[0]["reverses_action_id"] == audit[1]["id"]
            assert audit[1]["undone"] is True

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
            assert third_party.status_code == 422
            third_party_bills = {bill["id"]: bill for bill in client.get("/api/bills").json()}
            assert not third_party_bills[external_out["id"]]["aggregate_excluded"]
            assert not third_party_bills[external_in["id"]]["aggregate_excluded"]
            assert client.post(f"/api/candidates/{external['id']}/undo").status_code == 409
            assert client.get("/api/dashboard").json()["spending"] == -60
    finally:
        app.dependency_overrides.clear()


def test_three_identical_bills_use_one_idempotent_duplicate_group(tmp_path):
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
            def create_bill(at):
                response = client.post("/api/bills", json={"occurred_at": at, "merchant": "Didi fixture", "amount": -17.1, "account_name": "wallet"})
                assert response.status_code == 201
                return response.json()

            first, second, third = create_bill("2026-08-25T09:00:00"), create_bill("2026-08-25T09:01:00"), create_bill("2026-08-25T09:02:00")
            candidates = client.get("/api/candidates/page?candidate_type=duplicate").json()
            assert candidates["total"] == 1
            candidate = candidates["items"][0]
            assert {bill["id"] for bill in candidate["member_bills"]} == {first["id"], second["id"], third["id"]}
            detail = client.get(f"/api/candidates/{candidate['id']}/detail").json()
            assert len(detail["members"]) == 3
            assert {help["action"] for help in detail["decision_help"]} >= {"保留 A/B", "不是重复（拒绝建议）", "稍后处理"}
            assert round(client.get("/api/dashboard").json()["spending"], 2) == -51.3

            retained = client.post(f"/api/candidates/{candidate['id']}", json={"action": "resolve_duplicate", "retained_bill_id": first["id"]})
            assert retained.status_code == 200
            assert round(client.get("/api/dashboard").json()["spending"], 2) == -17.1
            assert client.post(f"/api/candidates/{candidate['id']}", json={"action": "resolve_duplicate", "retained_bill_id": first["id"]}).status_code == 200
            assert round(client.get("/api/dashboard").json()["spending"], 2) == -17.1
            assert client.post(f"/api/candidates/{candidate['id']}/undo").json()["status"] == "pending"
            assert round(client.get("/api/dashboard").json()["spending"], 2) == -51.3

            rejected = client.post(f"/api/candidates/{candidate['id']}", json={"action": "reject_duplicate"})
            assert rejected.status_code == 200 and rejected.json()["status"] == "duplicate_rejected"
            assert round(client.get("/api/dashboard").json()["spending"], 2) == -51.3
            assert client.post(f"/api/candidates/{candidate['id']}/undo").json()["status"] == "pending"
            deferred = client.post(f"/api/candidates/{candidate['id']}", json={"action": "deferred"})
            assert deferred.status_code == 200 and deferred.json()["status"] == "deferred"
            assert round(client.get("/api/dashboard").json()["spending"], 2) == -51.3
    finally:
        app.dependency_overrides.clear()


def test_duplicate_group_migration_merges_historical_subsets_and_reloads(tmp_path):
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
            bills = [client.post("/api/bills", json={"occurred_at": f"2026-08-25T10:0{index}:00", "merchant": "Didi migration", "amount": -8.0, "account_name": "wallet"}).json() for index in range(4)]
            with session_factory() as db:
                db.add_all([
                    ReviewCandidate(candidate_type="duplicate", bill_id=bills[0]["id"], related_bill_id=bills[1]["id"], member_bill_ids=json.dumps([bills[0]["id"], bills[1]["id"]]), confidence=.92, reason="legacy AB", status="pending", created_at=datetime.now()),
                    ReviewCandidate(candidate_type="duplicate", bill_id=bills[0]["id"], related_bill_id=bills[2]["id"], member_bill_ids=json.dumps([bills[0]["id"], bills[1]["id"], bills[2]["id"]]), confidence=.92, reason="legacy ABC", status="pending", created_at=datetime.now()),
                ])
                db.commit()
            page = client.get("/api/candidates/page?candidate_type=duplicate").json()
            assert page["total"] == 1
            candidate = page["items"][0]
            assert {member["id"] for member in candidate["member_bills"]} == {bill["id"] for bill in bills}
            assert round(client.get("/api/dashboard").json()["spending"], 2) == -32.0
            assert client.post(f"/api/candidates/{candidate['id']}", json={"action": "resolve_duplicate", "retained_bill_id": bills[0]["id"]}).status_code == 200
            assert round(client.get("/api/dashboard").json()["spending"], 2) == -8.0
            assert client.get("/api/candidates/page?candidate_type=duplicate").json()["total"] == 1
            assert client.post(f"/api/candidates/{candidate['id']}/undo").status_code == 200
            assert round(client.get("/api/dashboard").json()["spending"], 2) == -32.0
    finally:
        app.dependency_overrides.clear()


def test_read_only_database_observer_metadata_and_pagination(tmp_path):
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
        for number in range(3):
            db.add(Bill(occurred_at=datetime(2026, 8, 26, 9, number), merchant=f"observer-{number}", note="x" * (610 if number == 0 else 1), amount=-number - 1, account_name="local", category="uncategorized", tags=""))
        db.commit()

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            catalog = client.get("/api/database/tables")
            assert catalog.status_code == 200
            tables = {table["name"]: table for table in catalog.json()["tables"]}
            assert tables["bills"]["row_count"] == 3
            assert {column["name"] for column in tables["bills"]["columns"]} >= {"id", "merchant", "amount"}
            assert any(column["name"] == "id" and column["primary_key"] for column in tables["bills"]["columns"])
            assert "sqlite_sequence" not in tables

            first = client.get("/api/database/tables/bills?page=1&page_size=2")
            second = client.get("/api/database/tables/bills?page=2&page_size=2")
            assert [response.status_code for response in (first, second)] == [200, 200]
            assert first.json()["total"] == second.json()["total"] == 3
            assert [row["id"] for row in first.json()["rows"] + second.json()["rows"]] == [1, 2, 3]
            assert first.json()["rows"][0]["note"].endswith("… [truncated]")
            assert first.json()["sort"] == {"columns": ["id"], "order": "asc"}

            empty = client.get("/api/database/tables/asset_snapshots?page=1&page_size=25")
            assert empty.status_code == 200 and empty.json()["total"] == 0 and empty.json()["rows"] == []
            assert client.get("/api/database/tables/not_a_table").status_code == 404
            assert client.get("/api/database/tables/bills?page=0").status_code == 422
            assert client.get("/api/database/tables/bills?page_size=101").status_code == 422
            assert client.get("/api/dashboard").status_code == 200
            assert client.get("/api/transactions?page=1&page_size=2").json()["total"] == 3

            workspace = client.get("/static/workspace-next.js").text
            assert 'data-page="database"' in workspace
            assert "/api/database/tables" in workspace
            assert 'data-ascii-fallback="[DB]"' in workspace
    finally:
        app.dependency_overrides.clear()


def test_review_undo_and_partial_refund_allocation_are_append_only(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'review.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        _seed_json_tag_definitions(db)

    def override_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            expense = client.post("/api/bills", json={"occurred_at": "2026-08-25T10:00:00", "merchant": "expense", "amount": -300, "account_name": "wallet-a"}).json()
            other_expense = client.post("/api/bills", json={"occurred_at": "2026-08-25T11:00:00", "merchant": "other expense", "amount": -200, "account_name": "wallet-a"}).json()
            refund = client.post("/api/bills", json={"occurred_at": "2026-08-26T10:00:00", "merchant": "refund", "amount": 100, "account_name": "wallet-a"}).json()
            with session_factory() as db:
                db.add(LedgerOrigin(bill_id=expense["id"], source_type="wechat", source_reference="expense-raw", raw_payload='{"原始账户":"wallet-a"}'))
                db.commit()

            revised = client.post(f"/api/bills/{expense['id']}/tags", json={"strategy": "manual", "category": "餐饮", "tags": ["消费"], "reason": "manual correction", "idempotency_key": "tag-1"})
            assert revised.status_code == 201
            repeated = client.post(f"/api/bills/{expense['id']}/tags", json={"strategy": "manual", "category": "餐饮", "tags": ["消费"], "reason": "manual correction", "idempotency_key": "tag-1"})
            assert repeated.status_code == 201 and repeated.json()["id"] == revised.json()["id"]
            undone_tag = client.post(f"/api/bills/{expense['id']}/tags/{revised.json()['id']}/undo", json={"reason": "mistake"})
            assert undone_tag.status_code == 200
            assert undone_tag.json()["action"] == "undo"
            current_bill = next(item for item in client.get("/api/bills").json() if item["id"] == expense["id"])
            assert current_bill["category"] == "未分类"
            assert current_bill["tag_state"]["category"] == "unclassified"
            tag_history = client.get(f"/api/bills/{expense['id']}/tags").json()
            assert [item["action"] for item in tag_history[:2]] == ["undo", "confirm"]
            assert tag_history[0]["reverses_audit_id"] == revised.json()["id"]
            assert client.post(f"/api/bills/{expense['id']}/tags", json={"strategy": "manual", "category": "餐饮", "tags": ["消费"], "reason": "manual correction", "idempotency_key": "tag-1"}).status_code == 409

            account = client.put(f"/api/transactions/{expense['id']}/account", json={"account_name": "wallet-b", "reason": "correct owner", "idempotency_key": "account-1"})
            assert account.status_code == 200 and account.json()["account_name"] == "wallet-b"
            undone_account = client.post(f"/api/transactions/{expense['id']}/account-revisions/{account.json()['revision_id']}/undo", json={"reason": "restore"})
            assert undone_account.status_code == 200 and undone_account.json()["account_name"] == "wallet-a"
            account_history = client.get(f"/api/transactions/{expense['id']}/account-revisions").json()
            assert [item["action"] for item in account_history] == ["confirm", "undo"]
            assert account_history[1]["reverses_revision_id"] == account.json()["revision_id"]
            assert client.put(f"/api/transactions/{expense['id']}/account", json={"account_name": "wallet-b", "reason": "correct owner", "idempotency_key": "account-1"}).status_code == 409

            ordered_bill = client.post("/api/bills", json={"occurred_at": "2026-08-25T11:30:00", "merchant": "ordered account", "amount": -10, "account_name": "wallet-a"}).json()
            ordered_revisions = []
            for index, account_name in enumerate(("wallet-b", "wallet-c", "wallet-b"), start=1):
                response = client.put(
                    f"/api/transactions/{ordered_bill['id']}/account",
                    json={"account_name": account_name, "idempotency_key": f"ordered-account-{index}"},
                )
                assert response.status_code == 200
                ordered_revisions.append(response.json()["revision_id"])
            out_of_order = client.post(
                f"/api/transactions/{ordered_bill['id']}/account-revisions/{ordered_revisions[0]}/undo",
                json={"reason": "must reject stale revision"},
            )
            assert out_of_order.status_code == 409
            ordered_current = next(item for item in client.get("/api/bills").json() if item["id"] == ordered_bill["id"])
            assert ordered_current["account_name"] == "wallet-b"

            first = client.post("/api/refund-allocations", json={"refund_bill_id": refund["id"], "expense_bill_id": expense["id"], "amount": 60, "reason": "partial refund", "idempotency_key": "refund-60"})
            assert first.status_code == 201
            assert client.post("/api/refund-allocations", json={"refund_bill_id": refund["id"], "expense_bill_id": expense["id"], "amount": 60, "reason": "partial refund", "idempotency_key": "refund-60"}).json()["id"] == first.json()["id"]
            assert client.get("/api/dashboard").json()["refund_offset"] == 60
            single_undo = client.post(f"/api/refund-allocations/{first.json()['id']}/undo", json={"reason": "single allocation check"})
            assert single_undo.status_code == 200
            assert client.get("/api/dashboard").json()["refund_offset"] == 0
            assert client.post("/api/refund-allocations", json={"refund_bill_id": refund["id"], "expense_bill_id": expense["id"], "amount": 60, "reason": "partial refund", "idempotency_key": "refund-60"}).status_code == 409

            first_multi = client.post("/api/refund-allocations", json={"refund_bill_id": refund["id"], "expense_bill_id": expense["id"], "amount": 60, "reason": "multi allocation", "idempotency_key": "refund-60-multi"})
            assert first_multi.status_code == 201
            second = client.post("/api/refund-allocations", json={"refund_bill_id": refund["id"], "expense_bill_id": other_expense["id"], "amount": 40, "reason": "remaining refund", "idempotency_key": "refund-40"})
            assert second.status_code == 201
            rejected = client.post("/api/refund-allocations", json={"refund_bill_id": refund["id"], "expense_bill_id": expense["id"], "amount": 1, "reason": "over limit", "idempotency_key": "refund-over"})
            assert rejected.status_code == 422
            assert client.get("/api/dashboard").json()["refund_offset"] == 100

            undone = client.post(f"/api/refund-allocations/{first_multi.json()['id']}/undo", json={"reason": "wrong allocation"})
            assert undone.status_code == 200 and undone.json()["status"] == "revoked"
            assert client.get("/api/dashboard").json()["refund_offset"] == 40
            audits = client.get(f"/api/refund-allocations/{first_multi.json()['id']}/audits").json()
            assert [audit["action"] for audit in audits] == ["confirm", "revoke"]
            assert all(audit["actor"] == "local-user" for audit in audits)

            with session_factory() as db:
                origin = db.scalar(select(LedgerOrigin).where(LedgerOrigin.bill_id == expense["id"]))
                assert origin.raw_payload == '{"原始账户":"wallet-a"}'
    finally:
        app.dependency_overrides.clear()


def test_refund_limits_are_serialized_across_independent_sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'concurrent.db'}", connect_args={"check_same_thread": False, "timeout": 2})
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
            expense_a = client.post("/api/bills", json={"occurred_at": "2026-08-25T10:00:00", "merchant": "expense-a", "amount": -100}).json()
            expense_b = client.post("/api/bills", json={"occurred_at": "2026-08-25T11:00:00", "merchant": "expense-b", "amount": -100}).json()
            refund = client.post("/api/bills", json={"occurred_at": "2026-08-26T10:00:00", "merchant": "refund", "amount": 100}).json()

        payloads = [
            {"refund_bill_id": refund["id"], "expense_bill_id": expense_a["id"], "amount": 60, "idempotency_key": "shared-refund-a"},
            {"refund_bill_id": refund["id"], "expense_bill_id": expense_b["id"], "amount": 60, "idempotency_key": "shared-refund-b"},
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda payload: TestClient(app).post("/api/refund-allocations", json=payload), payloads))
        assert sorted(response.status_code for response in responses) == [201, 422]

        with session_factory() as db:
            confirmed = db.execute(text("SELECT COALESCE(SUM(amount), 0) FROM refund_allocations WHERE status = 'confirmed' AND refund_bill_id = :id"), {"id": refund["id"]}).scalar_one()
            assert confirmed == 60

        with TestClient(app) as client:
            shared_expense = client.post("/api/bills", json={"occurred_at": "2026-08-27T10:00:00", "merchant": "shared-expense", "amount": -100}).json()
            refund_a = client.post("/api/bills", json={"occurred_at": "2026-08-28T10:00:00", "merchant": "refund-a", "amount": 70}).json()
            refund_b = client.post("/api/bills", json={"occurred_at": "2026-08-28T11:00:00", "merchant": "refund-b", "amount": 50}).json()
        expense_payloads = [
            {"refund_bill_id": refund_a["id"], "expense_bill_id": shared_expense["id"], "amount": 70, "idempotency_key": "shared-expense-a"},
            {"refund_bill_id": refund_b["id"], "expense_bill_id": shared_expense["id"], "amount": 50, "idempotency_key": "shared-expense-b"},
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda payload: TestClient(app).post("/api/refund-allocations", json=payload), expense_payloads))
        assert sorted(response.status_code for response in responses) == [201, 422]
        with session_factory() as db:
            confirmed = db.execute(text("SELECT COALESCE(SUM(amount), 0) FROM refund_allocations WHERE status = 'confirmed' AND expense_bill_id = :id"), {"id": shared_expense["id"]}).scalar_one()
            assert confirmed <= 100

        with engine.connect() as locked:
            locked.exec_driver_sql("BEGIN IMMEDIATE")
            busy = TestClient(app).post("/api/refund-allocations", json={"refund_bill_id": refund_a["id"], "expense_bill_id": expense_a["id"], "amount": 1, "idempotency_key": "busy-lock"})
            assert busy.status_code == 409
            locked.exec_driver_sql("ROLLBACK")
        with session_factory() as db:
            assert db.execute(text("SELECT COUNT(*) FROM refund_allocations WHERE idempotency_key = 'busy-lock'")).scalar_one() == 0
    finally:
        app.dependency_overrides.clear()


def test_effective_ledger_uses_one_filtered_set_for_list_summary_and_drilldown(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger-contract.db'}", connect_args={"check_same_thread": False})
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
            expense = client.post("/api/bills", json={"occurred_at": "2026-08-25T10:00:00", "merchant": "kept expense", "amount": -300, "account_name": "wallet-a"}).json()
            refund = client.post("/api/bills", json={"occurred_at": "2026-08-25T11:00:00", "merchant": "refund", "amount": 100, "account_name": "wallet-a"}).json()
            client.post("/api/refund-allocations", json={"refund_bill_id": refund["id"], "expense_bill_id": expense["id"], "amount": 60, "idempotency_key": "ledger-refund"})
            duplicate_a = client.post("/api/bills", json={"occurred_at": "2026-08-25T12:00:00", "merchant": "duplicate", "amount": -20, "account_name": "wallet-a"}).json()
            duplicate_b = client.post("/api/bills", json={"occurred_at": "2026-08-25T12:01:00", "merchant": "duplicate", "amount": -20, "account_name": "wallet-a"}).json()
            candidate = next(item for item in client.get("/api/candidates").json() if item["candidate_type"] == "duplicate")
            client.post(f"/api/candidates/{candidate['id']}", json={"action": "resolve_duplicate", "retained_bill_id": duplicate_a["id"]})
            other_a = client.post("/api/bills", json={"occurred_at": "2026-08-25T13:00:00", "merchant": "other duplicate", "amount": -50, "account_name": "wallet-b"}).json()
            other_b = client.post("/api/bills", json={"occurred_at": "2026-08-25T13:01:00", "merchant": "other duplicate", "amount": -50, "account_name": "wallet-b"}).json()
            other_candidate = next(
                item
                for item in client.get("/api/candidates").json()
                if item["candidate_type"] == "duplicate" and other_a["id"] in {bill["id"] for bill in item["member_bills"]}
            )
            client.post(f"/api/candidates/{other_candidate['id']}", json={"action": "resolve_duplicate", "retained_bill_id": other_a["id"]})

            query = "date_from=2026-08-25&date_to=2026-08-25&account=wallet-a"
            listed = client.get(f"/api/transactions?{query}&page_size=100").json()
            summary = client.get(f"/api/dashboard?{query}").json()
            drilldown = client.get(f"/api/ledger/drilldown?{query}").json()
            listed_ids = {item["id"] for item in listed["items"]}
            assert duplicate_b["id"] not in listed_ids
            assert listed_ids == set(summary["transaction_ids"]) == set(drilldown["transaction_ids"])
            assert summary["income"] == 0
            assert summary["spending"] == -320
            assert summary["refund_offset"] == 60
            assert summary["net"] == -260
            assert summary["cash_net"] == -220
            assert summary["unallocated_refund"] == 40
            assert summary["effective_count"] == len(listed_ids)
            assert summary["basis_version"] == drilldown["basis_version"] == "review-foundation-v2"
            assert drilldown["filters"] == summary["filters"] == listed["filters"]
            assert any(item["bill_id"] == duplicate_b["id"] and item["reason"] == "confirmed_duplicate" for item in drilldown["excluded"])
            assert all(item["bill_id"] != other_b["id"] for item in drilldown["excluded"])
            assert set(drilldown["composition"]["net"]) == listed_ids
            assert drilldown["composition"]["refund_offset"][0]["expense_bill_id"] == expense["id"]
            expense_detail = next(item for item in drilldown["transactions"] if item["bill"]["id"] == expense["id"])
            assert {"source", "tag_audits", "account_revisions", "candidate_ids", "refund_allocations"} <= expense_detail.keys()
            assert expense_detail["refund_allocations"][0]["amount"] == 60
    finally:
        app.dependency_overrides.clear()
