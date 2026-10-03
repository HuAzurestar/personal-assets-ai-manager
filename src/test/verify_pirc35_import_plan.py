"""Actual 2500-row read-only selection/disclosure, isolated fictional SQLite."""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright
from serve_m2_ui import prepare_app
from browser_artifact import viewport_evidence


def run():
    with tempfile.TemporaryDirectory(prefix="paam-pirc35-import-plan-") as temporary:
        app = prepare_app(Path(temporary))
        from backend.core import target_database
        from test_pirc35_import_duplicate import manifest
        def snapshot():
            with target_database.SessionLocal() as db:
                return manifest(SimpleNamespace(db=db))
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
                    page = browser.new_page(viewport={"width": 1280, "height": 900})
                    errors, puts, writes = [], [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("request", lambda request: puts.append(request.post_data_json)
                        if request.method == "PUT" and "/paam/import/v1/preview/" in request.url else None)
                    page.on("request", lambda request: writes.append(request.url)
                        if request.method == "POST" and request.url.endswith("/confirm") else None)
                    page.goto(base + "/#workbench/import")
                    page.locator('[data-action="import-step"][data-step="2"]').last.click()
                    upload = page.locator('[data-form="import-preview"]')
                    expect(upload).to_be_visible()
                    header = "建设银行个人交易明细\n账号：990000000000001234\n姓名：Mock测试用户\n币种：人民币\n摘要,币别,交易日期,交易金额,账户余额,交易地点/附言,对方账号与户名\n"
                    # Unique exact accounting cores; no risk consent is inferred.
                    content = header + "".join(f"Mock真实{i},CNY,2024-01-01,-{i}.00,10000.00,Mock用途{i},Mock商户\n" for i in range(1, 2501))
                    upload.locator('[name="files"]').set_input_files({"name":"Mock2500.csv","mimeType":"text/csv","buffer":content.encode()})
                    upload.locator('[data-action="preview-import"]').click()
                    expect(page.locator('[data-batch-row]')).to_have_count(20, timeout=30000)
                    before = snapshot()
                    # Fail page two of the explicit all-scope read. Page one
                    # must not become a partial selection or a hidden write.
                    def fail_second(route):
                        if "page_index=2" in route.request.url and "page_size=100" in route.request.url:
                            route.fulfill(status=503,content_type="application/json",body=json.dumps(
                                {"status":503,"message":"Mock second page unavailable","body":{"code":"QUERY_BUSY"}}))
                        else:
                            route.continue_()
                    pattern = "**/paam/import/v1/preview/*/row/list?*"
                    page.route(pattern,fail_second)
                    page.locator('[data-batch-select-scope]').click()
                    expect(page.locator('[data-batch-status]')).to_contain_text("原选择保留",timeout=30000)
                    expect(page.locator('[data-batch-selection]')).to_contain_text("0 行")
                    expect(page.locator('[data-batch-select-scope]')).to_be_enabled(timeout=15000)
                    assert puts == writes == [] and snapshot() == before
                    page.unroute(pattern,fail_second)
                    page.locator('[data-batch-select-scope]').click()
                    expect(page.locator('[data-batch-select-scope]')).to_be_enabled(timeout=30000)
                    assert "2500 行" in page.locator('[data-batch-selection]').inner_text(), page.locator('[data-batch-status]').inner_text()
                    expect(page.locator('[data-batch-plan]')).to_be_disabled()
                    expect(page.locator('[data-batch-confirm]')).to_be_disabled()
                    page.locator('[data-batch-save]').click()
                    expect(page.locator('[data-batch-plan]')).to_be_enabled(timeout=30000)
                    assert [len(body["choices"]) for body in puts] == [1000,1000,500]
                    assert len({(row["file_id"],row["source_row_number"]) for body in puts for row in body["choices"]}) == 2500
                    assert min(row["source_row_number"] for body in puts for row in body["choices"]) == 6
                    assert max(row["source_row_number"] for body in puts for row in body["choices"]) == 2505
                    expect(page.locator('[data-batch-confirm]')).to_be_disabled()
                    page.locator('[data-batch-plan]').click()
                    plan = page.locator('[data-import-operation]')
                    expect(plan).to_contain_text("本次明确选择 2500 行",timeout=30000)
                    expect(plan.locator('[data-plan-batches] [data-plan-batch]')).to_have_count(3)
                    expect(plan).to_contain_text("此前成功批保留")
                    expect(plan).to_contain_text("不自动重发 POST")
                    expect(plan).to_contain_text("第 3 批 · 500 行")
                    expect(plan).to_contain_text("第 2006–2505 行")
                    plan.locator('[data-plan-batch]').last.click()
                    expect(plan.locator('[data-plan-batch-detail]')).to_contain_text("第 3 批")
                    expect(plan.locator('[data-plan-batch-detail]')).to_contain_text("真实新增 500")
                    pairs = plan.locator('[data-plan-pairs]')
                    pairs.locator('xpath=..').locator('summary').click()
                    expect(pairs).to_contain_text("1–20 / 500")
                    expect(pairs.locator('[data-plan-prev]')).to_be_disabled()
                    for _ in range(24):
                        pairs.locator('[data-plan-next]').click()
                    expect(pairs).to_contain_text("481–500 / 500")
                    expect(pairs).to_contain_text("第 2505 行")
                    expect(pairs.locator('[data-plan-next]')).to_be_disabled()
                    # The readonly panel must not accidentally enable local
                    # pager boundaries when the outer view refreshes controls.
                    assert writes == [] and snapshot() == before
                    page.evaluate("document.querySelector('[data-batch-confirm]').onclick()")
                    assert writes == []  # Programmatic click cannot bypass >1k.
                    for width in (1440,1280,820,390):
                        page.set_viewport_size({"width":width,"height":900})
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                        if width >= 1280:
                            assert max(pairs.locator('.import-plan-record').evaluate_all('nodes => nodes.map(node => node.getBoundingClientRect().height)')) <= 110
                        expect(plan.locator('[data-plan-batch-detail]')).to_be_visible()
                        plan.scroll_into_view_if_needed()
                        viewport_evidence(page,f"fix-import-plan-{width}")
                    page.locator('[data-batch-clear]').click()
                    expect(page.locator('[data-import-operation]')).to_have_count(0)
                    expect(page.locator('[data-batch-selection]')).to_contain_text("0 行")
                    assert errors == [] and writes == [] and snapshot() == before
                    # A complete risk disclosure is not consent to create more
                    # real cash. Both DOM disablement and the click handler
                    # block the old <=1000 writer when this plan is unresolved.
                    page.set_viewport_size({"width":1280,"height":900})
                    page.locator('[data-action="import-step"][data-step="2"]').first.click()
                    expect(upload).to_be_visible()
                    risk = header + "Mock风险A,CNY,2024-01-01,-77.00,10000.00,Mock用途A,Mock商户\nMock风险B,CNY,2024-01-01,-77.00,10000.00,Mock用途B,Mock商户\n"
                    upload.locator('[name="files"]').set_input_files({"name":"MockRisk.csv","mimeType":"text/csv","buffer":risk.encode()})
                    upload.locator('[data-action="preview-import"]').click()
                    expect(page.locator('[data-batch-row]')).to_have_count(2,timeout=15000)
                    risk_before = snapshot()
                    page.locator('[data-batch-select-scope]').click()
                    expect(page.locator('[data-batch-selection]')).to_contain_text("2 行")
                    page.locator('[data-batch-save]').click()
                    expect(page.locator('[data-batch-plan]')).to_be_enabled()
                    page.locator('[data-batch-plan]').click()
                    expect(page.locator('[data-import-operation]')).to_contain_text("存在阻断或未解决事项",timeout=15000)
                    expect(page.locator('[data-batch-confirm]')).to_be_disabled()
                    page.evaluate("document.querySelector('[data-batch-confirm]').onclick()")
                    assert writes == [] and snapshot() == risk_before
                    assert [len(body["choices"]) for body in puts] == [1000,1000,500,2]
                    assert errors == []
                    browser.close()
                print("PIRC-35 2500-row complete read-only operation plan, three choice PUTs, no financial writes passed")
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            target_database.engine.dispose()


if __name__ == "__main__":
    run()
