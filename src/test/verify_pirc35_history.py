"""Actual filtered import statistics over a new fictional database; zero UI writes."""
from datetime import datetime, timezone
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlparse

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright
from serve_m2_ui import prepare_app
from browser_artifact import viewport_evidence
from browser_list import assert_full_list_text, assert_list_readability


def run():
    with tempfile.TemporaryDirectory(prefix="paam-pirc35-history-") as temporary:
        app = prepare_app(Path(temporary))
        from backend.core import target_database
        from backend.entity import TransactionImportFile
        with target_database.SessionLocal() as db:
            samples = [(101, 1), (101, 3), (102, 1), (201, 1), (201, 1), (201, 1)] + [(203, 1)] * 30
            for index, (source, status) in enumerate(samples, 1):
                db.add(TransactionImportFile(filename=f"Mock history {index}" + (
                    ' 完全虚构的长中文账单文件名用于检查导入历史内容可以完整阅读而不被隐藏' if index == 35 else '') + '.csv', source_type=source,
                    status=status, file_format=1, sha256=f"{index:064x}",
                    created_time=datetime(2026, 9, 1, 0, index, tzinfo=timezone.utc),
                    updated_time=datetime(2026, 9, 1, 0, index, tzinfo=timezone.utc)))
            db.commit()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        try:
            with httpx.Client(base_url=base, trust_env=False, timeout=30) as client:
                for _ in range(100):
                    try:
                        if client.get("/api/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError("fictional app did not start")
                ordered = client.get('/paam/import/v1/import_file/list', params={
                    'page_size': 10, 'sorter': '[{"key":"created_time","direction":"desc"}]'}).json()['body']['items']
                assert ordered[0]['filename'] == 'Mock history 36.csv', ordered
                assert ordered[1]['filename'].startswith('Mock history 35 完全虚构'), ordered
                before = {path: client.get(path).json()["body"]["total"] for path in (
                    "/paam/import/v1/import_file/list", "/paam/ledger/v1/flow/list", "/paam/ledger/v1/transaction_fact/list")}
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                    page = browser.new_page(viewport={"width": 1280, "height": 800})
                    errors, writes, reads = [], [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("request", lambda request: reads.append(request.url) if request.method == "GET" else writes.append(request.url))
                    page.goto(base + "/#workbench/import/history")
                    form = page.locator('[data-form="history-filter"]')
                    files = page.locator('[data-action="import-file-detail"]')
                    metric = page.locator('[data-history-summary] .history-metric').first.locator('strong')
                    expect(files).to_have_count(10)
                    expect(metric).to_have_text('36')
                    for width in (1440, 1280, 1100, 820, 390, 320):
                        page.set_viewport_size({'width': width, 'height': 900})
                        assert_list_readability(page, files.first, files.first.locator('.batch-file strong'),
                            files.first.locator('.batch-file small'))
                        long_title = files.locator('.batch-file strong').filter(has_text='Mock history 35')
                        assert_full_list_text(long_title, '完全虚构的长中文账单文件名用于检查导入历史内容可以完整阅读而不被隐藏')
                        files.first.evaluate('row => window.scrollBy(0, row.getBoundingClientRect().top - 180)')
                        viewport_evidence(page, f'dev17-list-history-{width}')
                        page.evaluate('window.scrollTo(0, 0)')
                    page.set_viewport_size({'width': 1280, 'height': 800})
                    form.locator('[name="source_type"]').select_option('102')
                    expect(files).to_have_count(1)
                    expect(metric).to_have_text('1')
                    form.locator('[name="status"]').select_option('3')
                    expect(files).to_have_count(0)
                    expect(metric).to_have_text('0')
                    expect(page.locator('[data-history-results]')).to_contain_text('没有匹配的导入记录')
                    form.locator('[name="source_type"]').select_option('101')
                    expect(files).to_have_count(1)
                    expect(metric).to_have_text('1')
                    # The periodic refresh must use the same AND filter as the list.
                    with page.expect_response(lambda response: '/import_file/summary' in response.url, timeout=10000):
                        page.locator('.history-panel h2').click()
                    expect(files).to_have_count(1)
                    expect(metric).to_have_text('1')
                    latest_list = next(url for url in reversed(reads) if '/import_file/list?' in url)
                    latest_summary = next(url for url in reversed(reads) if '/import_file/summary' in url)
                    assert parse_qs(urlparse(latest_summary).query)['filter'] == parse_qs(urlparse(latest_list).query)['filter']
                    assert set(parse_qs(urlparse(latest_summary).query)) == {'filter'}
                    form.locator('[name="source_type"]').select_option(value=[''])
                    expect(files).to_have_count(1)
                    form.locator('[name="status"]').select_option(value=[''])
                    expect(files).to_have_count(10)
                    expect(metric).to_have_text('36')
                    form.locator('[name="source_type"]').select_option('203')
                    expect(metric).to_have_text('30')
                    page.locator('[data-action="history-page"][data-value="2"]').click()
                    expect(form).to_have_attribute('data-page', '2')
                    expect(page.locator('[data-history-results] .range')).to_contain_text('11–20')
                    origin = page.url
                    assert 'source_type=203' in origin and 'page=2' in origin
                    target = files.nth(4)
                    target_id = target.get_attribute('data-id')
                    target.evaluate('row => window.scrollBy(0,row.getBoundingClientRect().top - 160)')
                    page.wait_for_function('history.state?.paamView?.y > 0')
                    top = target.bounding_box()['y']
                    target.click()
                    page.locator('.inspection-workspace[open] [data-close]').click()
                    assert abs(target.bounding_box()['y'] - top) < 3
                    # Route directly without scrolling to the topbar first.
                    page.evaluate("location.hash = '#workbench/account'")
                    expect(page.locator('[data-account-management]')).to_be_visible()
                    page.go_back()
                    expect(files).to_have_count(10)
                    expect(form).to_have_attribute('data-page', '2')
                    expect(form.locator('[name="source_type"]')).to_have_value('203')
                    expect(metric).to_have_text('30')
                    expect(page.locator('#page-content')).not_to_have_attribute('aria-busy', 'true')
                    assert page.url == origin
                    target = page.locator(f'[data-history-file="{target_id}"]')
                    assert abs(target.bounding_box()['y'] - top) < 3
                    page.reload()
                    expect(form).to_have_attribute('data-page', '2')
                    expect(metric).to_have_text('30')
                    expect(page.locator('#page-content')).not_to_have_attribute('aria-busy', 'true')
                    assert abs(target.bounding_box()['y'] - top) < 3
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    viewport_evidence(page, 'dev17-import-history-filtered')
                    assert not errors and not writes, (errors, writes)
                    browser.close()
                after = {path: client.get(path).json()["body"]["total"] for path in before}
                assert before == after
                print("PASS actual import history all/source/status/AND/zero/clear/background/page-two/detail/back/reload/narrow; zero UI writes")
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            target_database.engine.dispose()


if __name__ == '__main__':
    run()
