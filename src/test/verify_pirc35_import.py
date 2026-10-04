"""Actual browser, fictional statements, selected scope and lost response."""
import os
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright
from serve_m2_ui import prepare_app
from browser_artifact import viewport_evidence


def run():
    with tempfile.TemporaryDirectory(prefix="paam-pirc35-import-") as temporary:
        app = prepare_app(Path(temporary))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        try:
            with httpx.Client(base_url=base, trust_env=False) as client:
                for _ in range(100):
                    try:
                        if client.get("/api/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError("fictional app did not start")
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                    page = browser.new_page(viewport={"width": 1280, "height": 800})
                    errors, confirmations = [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("request", lambda request: confirmations.append(request.post_data_json) if request.method == "POST" and request.url.endswith("/confirm") else None)
                    page.goto(base + "/#workbench/import")
                    expect(page.locator('[data-form="import-preview"]')).to_be_visible()
                    page.locator('[data-action="import-step"][data-step="2"]').last.click()
                    upload = page.locator('[data-form="import-preview"]')
                    fixtures = Path(__file__).parent / "fixtures" / "pirc35"
                    upload.locator('[name="files"]').set_input_files(fixtures / "ccb-2.csv")
                    upload.locator('[data-action="preview-import"]').click()
                    rows = page.locator("[data-batch-row]")
                    expect(rows).to_have_count(20, timeout=15000)
                    # R02: compact import columns, not the five-column cash editor.
                    assert rows.first.get_attribute("class") == "import-batch-row"
                    for width in (1440, 1280):
                        page.set_viewport_size({"width": width, "height": 900})
                        assert max(rows.evaluate_all("nodes => nodes.map(node => node.getBoundingClientRect().height)")) <= 110
                        assert rows.first.locator(".import-batch-main").bounding_box()["width"] >= 200
                        viewport_evidence(page, f"fix-batch2-import-{width}")
                    for width in (820, 390):
                        page.set_viewport_size({"width": width, "height": 900})
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                        expect(rows.first.locator("[data-row-decision]")).to_be_visible()
                    page.set_viewport_size({"width": 1280, "height": 800})
                    expect(page.locator("[data-batch-confirm]")).to_be_disabled()
                    page.locator("[data-batch-select-page]").click()
                    expect(page.locator("[data-batch-selection]")).to_contain_text("20 行")
                    page.locator("[data-batch-save]").click()
                    expect(page.locator("[data-batch-confirm]")).to_be_enabled()
                    # An intent edit invalidates approval until a successful PUT.
                    rows.first.locator("[data-row-decision]").select_option("SKIP")
                    expect(page.locator("[data-batch-confirm]")).to_be_disabled()
                    rows.first.locator("[data-row-decision]").select_option("ACCEPT")
                    page.locator("[data-batch-save]").click()
                    expect(page.locator("[data-batch-confirm]")).to_be_enabled()
                    page.locator("[data-batch-confirm]").click()
                    expect(page.locator("[data-batch-files]")).to_contain_text("已接受 20", timeout=15000)
                    expect(page.locator("[data-batch-files]")).to_contain_text("剩余 4")
                    expect(rows.first.locator("[data-row-select]")).to_be_disabled()
                    assert len(confirmations) == 1 and len(confirmations[0]["selected_rows"]) == 20
                    assert "version" not in confirmations[0]
                    page.locator("[data-batch-next]").click()
                    expect(rows).to_have_count(4)
                    rows.first.locator("[data-row-account]").click()
                    picker = page.locator("dialog[open] [data-picker-items]")
                    expect(picker.locator(".picker-list-row")).to_have_count(1)
                    expect(picker).to_contain_text("建设银行")
                    expect(picker).to_contain_text("****1234")
                    expect(picker).to_contain_text("未分组")
                    picker_host = page.locator("dialog[open] [data-ref-picker]")
                    expect(picker_host.locator("[data-picker-word]")).to_be_enabled()
                    picker_host.locator("[data-picker-word]").fill("建设银行")
                    picker_host.locator("[data-picker-search]").click()
                    expect(picker_host.locator("[data-picker-scan-status]")).to_contain_text("本次扫描结束")
                    expect(picker.locator(".picker-list-row")).to_have_count(1)
                    candidate = picker.locator(".picker-list-row").first
                    assert candidate.locator("span").bounding_box()["width"] > candidate.bounding_box()["width"] * .65
                    assert candidate.locator("button").bounding_box()["width"] < 140
                    assert candidate.bounding_box()["height"] <= 70
                    viewport_evidence(page, "fix-batch2-source-picker")
                    candidate.locator("button").click()
                    expect(rows.first.locator("[data-row-ref]")).to_contain_text("建设银行")
                    expect(rows.first.locator("[data-row-ref]")).to_contain_text("****1234")
                    page.locator("[data-batch-select-page]").click()
                    page.locator("[data-batch-save]").click()
                    expect(page.locator("[data-batch-confirm]")).to_be_enabled()
                    # Server commits; browser receives an unknown-result error.
                    def dropped_reply(route):
                        response = route.fetch()
                        assert response.status == 200
                        route.fulfill(status=503, content_type="application/json",
                            body='{"status":503,"message":"fictional dropped reply","body":{"code":"RESULT_UNKNOWN","details":{}}}')
                    page.route("**/paam/import/v1/preview/*/confirm", dropped_reply)
                    page.locator("[data-batch-confirm]").click()
                    expect(page.locator("[data-batch-status]")).to_contain_text("结果未知", timeout=15000)
                    expect(page.locator("[data-batch-confirm]")).to_be_disabled()
                    assert page.evaluate("JSON.parse(localStorage.getItem('paam.import.pending.v1')).rows.length") == 4
                    # Terminal row statuses must not unlock unknown results
                    # while the original preview still reports CONFIRMING.
                    token = page.evaluate("JSON.parse(localStorage.getItem('paam.import.pending.v1')).token")
                    token_url = base + f"/paam/import/v1/preview/{token}"
                    def still_confirming(route):
                        response = route.fetch()
                        payload = response.json()
                        payload["body"]["status"] = "CONFIRMING"
                        route.fulfill(response=response, body=json.dumps(payload))
                    page.route(token_url, still_confirming)
                    page.locator("[data-batch-verify]").click()
                    expect(page.locator("[data-batch-verification]")).to_contain_text("已接受", timeout=15000)
                    expect(page.locator("[data-batch-verify]")).to_be_enabled()  # finally completed
                    expect(page.locator("[data-batch-observed]")).to_be_disabled()
                    assert page.evaluate("JSON.parse(localStorage.getItem('paam.import.pending.v1')).rows.length") == 4
                    assert len(confirmations) == 2
                    page.unroute(token_url, still_confirming)
                    page.locator("[data-batch-verify]").click()
                    expect(page.locator("[data-batch-verification]")).to_contain_text("已接受", timeout=15000)
                    expect(page.locator("[data-batch-verification]")).to_contain_text("CONFIRMED")
                    assert len(confirmations) == 2
                    expect(page.locator("[data-batch-observed]")).to_be_enabled()
                    page.locator("[data-batch-observed]").click()
                    expect(page.locator("[data-batch-files]")).to_contain_text("已接受 24")
                    assert page.evaluate("localStorage.getItem('paam.import.pending.v1')") is None
                    assert len(confirmations) == 2
                    page.unroute("**/paam/import/v1/preview/*/confirm", dropped_reply)
                    page.goto(base + "/#workbench/import/history")
                    expect(page.locator('[data-form="history-filter"]')).to_be_visible()
                    expect(page.locator('[data-action="import-file-detail"]')).to_have_count(1)
                    files = client.get("/paam/import/v1/import_file/list").json()["body"]["items"]
                    assert len(files) == 1 and files[0]["success_count"] == 24
                    # Persisted list is light; private raw evidence is fetched on demand.
                    page.goto(base + "/#details/import-file")
                    expect(page.locator('[data-action="import-file-detail"]')).to_have_count(1)
                    page.locator('[data-action="import-file-detail"]').click()
                    expect(page.locator(".inspection-workspace[open]")).to_be_visible()
                    page.locator("[data-source-evidence]").first.click()
                    expect(page.locator("dialog[open] .dialog-body").last).to_contain_text("当前可核验关系")
                    expect(page.locator("dialog[open] details summary").last).to_contain_text("JSON")
                    page.locator("dialog[open] [data-workbench-close]").click()
                    page.locator(".inspection-workspace[open] [data-close]").click()
                    # File diagnostics are not row problems or a successful empty preview.
                    page.goto(base + "/#workbench/import")
                    page.locator('[data-action="import-step"][data-step="2"]').first.click()
                    expect(page.locator('[data-form="import-preview"]')).to_be_visible()
                    upload = page.locator('[data-form="import-preview"]')
                    upload.locator('[name="files"]').set_input_files([
                        {"name": "Mock broken.csv", "mimeType": "text/csv", "buffer": b"not,a,statement"},
                        {"name": "Mock empty.csv", "mimeType": "text/csv", "buffer":
                            "建设银行个人交易明细\n账号：990000000000001234\n币种：人民币\n交易日期,交易金额,币别,摘要\n".encode()},
                        {"name": "Mock accepted.csv", "mimeType": "text/csv", "buffer": (fixtures / "ccb-2.csv").read_bytes()},
                    ])
                    upload.locator('[data-action="preview-import"]').click()
                    expect(page.locator('[data-file-parse-error]')).to_have_count(1, timeout=15000)
                    expect(page.locator('[data-file-parse-error]')).to_contain_text("解析失败")
                    expect(page.locator('[data-file-parse-empty]')).to_contain_text("有效空文件")
                    expect(page.locator('[data-batch-summary]')).to_contain_text("文件解析失败 1 · 行问题 0 · 总问题 1")
                    expect(page.locator('[data-batch-files]')).to_contain_text("解析成功：24 行")
                    expect(page.locator('[data-batch-files]')).to_contain_text("重新")
                    assert len(confirmations) == 2  # No extra cash, retry or implicit confirmation.
                    # Unchosen defaults must reflect row validity on both pages
                    # and complete scopes; explicit decisions are never reset.
                    header = '建设银行个人交易明细\n账号：990000000000001234\n币种：人民币\n摘要,币别,交易日期,交易金额\n'
                    mixed = header + 'Mock错误金额,CNY,2030-01-01,not-money\n'
                    mixed += ''.join(f'Mock默认正常{i},CNY,2030-01-{i:02},-{i}.50\n' for i in range(1, 22))
                    mixed += 'Mock错误日期,CNY,not-a-date,-99.00\n'
                    page.locator('[data-action="import-step"][data-step="2"]').first.click()
                    expect(upload).to_be_visible()
                    upload.locator('[name="files"]').set_input_files({
                        "name": "Mock defaults.csv", "mimeType": "text/csv", "buffer": mixed.encode()})
                    upload.locator('[data-action="preview-import"]').click()
                    expect(rows).to_have_count(20, timeout=15000)
                    expect(page.locator('[data-batch-summary]')).to_contain_text('拟新增 21')
                    expect(page.locator('[data-batch-summary]')).to_contain_text('行问题 2')
                    expect(rows.first.locator('[data-row-decision]')).to_have_value('SKIP')
                    expect(rows.nth(1).locator('[data-row-decision]')).to_have_value('ACCEPT')
                    page.locator('[data-batch-select-page]').click()
                    # The user may explicitly change either inferred decision.
                    rows.first.locator('[data-row-decision]').select_option('ACCEPT')
                    rows.nth(1).locator('[data-row-decision]').select_option('SKIP')
                    page.locator('[data-batch-save]').click()
                    expect(page.locator('[data-batch-confirm]')).to_be_disabled()
                    expect(page.locator('[data-batch-selection]')).to_contain_text('选择已保存')
                    expect(page.locator('[data-plan-issue-summary]')).to_contain_text('未解决 1 行')
                    assert len(confirmations) == 2  # Invalid manual acceptance is blocked before POST.
                    # Hold the preview metadata read while the old rows remain
                    # visible. Refresh must lock edits before that first await.
                    page.evaluate("""() => {
                        const original = window.fetch;
                        window.fetch = async (url, options) => {
                            if (/^\\/paam\\/import\\/v1\\/preview\\/[^/]+$/.test(String(url))
                                    && (!options?.method || options.method === 'GET')) {
                                window.fetch = original;
                                await new Promise(resolve => { window.releaseImportRefresh = resolve; });
                            }
                            return original(url, options);
                        };
                    }""")
                    page.locator('[data-batch-refresh]').click()
                    expect(rows.first.locator('[data-row-decision]')).to_be_disabled()
                    expect(page.locator('[data-batch-save]')).to_be_disabled()
                    page.evaluate('window.releaseImportRefresh()')
                    expect(rows.first.locator('[data-row-decision]')).to_be_enabled()
                    expect(rows.first.locator('[data-row-decision]')).to_have_value('ACCEPT')
                    expect(rows.nth(1).locator('[data-row-decision]')).to_have_value('SKIP')
                    rows.first.locator('[data-row-decision]').select_option('SKIP')
                    page.locator('[data-batch-select-scope]').click()
                    expect(page.locator('[data-batch-selection]')).to_contain_text('23 行')
                    expect(rows.nth(1).locator('[data-row-decision]')).to_have_value('SKIP')
                    page.locator('[data-batch-next]').click()
                    expect(rows).to_have_count(3)
                    expect(rows.last.locator('[data-row-decision]')).to_have_value('SKIP')
                    page.locator('[data-batch-save]').click()
                    try:
                        expect(page.locator('[data-batch-confirm]')).to_be_enabled()
                    except AssertionError:
                        print("Mixed import confirmation diagnostic", {
                            "selection": page.locator('[data-batch-selection]').inner_text(),
                            "status": page.locator('[data-batch-status]').inner_text(),
                            "risk": page.locator('[data-batch-risk-summary]').inner_text(),
                            "plan": page.locator('[data-batch-operation]').inner_text(),
                        })
                        raise
                    from backend.core import target_database
                    from backend.entity import TransactionFact, LedgerEntry, TransactionImportRow
                    from sqlalchemy import select, func
                    with target_database.SessionLocal() as db:
                        before_facts = db.scalar(select(func.count(TransactionFact.id)))
                        before_cash = db.scalar(select(func.count(LedgerEntry.id)))
                    file_id = int(rows.first.get_attribute('data-batch-row').split(':')[0])
                    viewport_evidence(page, 'fix-import-inferred-defaults')
                    page.locator('[data-batch-confirm]').click()
                    expect(page.locator('[data-batch-files]')).to_contain_text('已接受 20', timeout=15000)
                    expect(page.locator('[data-batch-files]')).to_contain_text('跳过 1 · 无效记录 2 · 剩余 0')
                    assert len(confirmations) == 3
                    with target_database.SessionLocal() as db:
                        assert db.scalar(select(func.count(TransactionFact.id))) == before_facts + 20
                        assert db.scalar(select(func.count(LedgerEntry.id))) == before_cash + 20
                        evidence = db.execute(select(TransactionImportRow.row_status,
                            TransactionImportRow.transaction_fact_id, TransactionImportRow.raw_payload,
                            TransactionImportRow.issue_code).where(
                                TransactionImportRow.transaction_import_file_id == file_id)).all()
                        assert len(evidence) == 23 and all(item.raw_payload for item in evidence)
                        abnormal = [item for item in evidence if item.row_status == 3]
                        assert len(abnormal) == 2
                        assert all(item.transaction_fact_id == 0 and item.issue_code == 'ROW_INVALID' for item in abnormal)
                        assert sum(item.row_status == 2 and item.transaction_fact_id == 0 for item in evidence) == 1
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.goto(base + "/#workbench/import")
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    assert errors == [], errors
                    browser.close()
                print("PIRC-35 fictional selected import, raw detail and lost-response browser workflow passed")
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            from backend.core import target_database
            target_database.engine.dispose()


if __name__ == "__main__":
    run()
