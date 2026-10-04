"""Actual filtered import statistics over a new fictional database; zero UI writes."""
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


def run():
    with tempfile.TemporaryDirectory(prefix="paam-pirc35-history-") as temporary:
        app = prepare_app(Path(temporary))
        from backend.core import target_database
        from backend.entity import TransactionImportFile
        with target_database.SessionLocal() as db:
            for index, (source, status) in enumerate([(101, 1), (101, 3), (102, 1), (201, 1), (201, 1), (201, 1)], 1):
                db.add(TransactionImportFile(filename=f"Mock history {index}.csv", source_type=source,
                    status=status, file_format=1, sha256=f"{index:064x}"))
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
                    expect(files).to_have_count(6)
                    expect(metric).to_have_text('6')
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
                    form.locator('[name="source_type"]').select_option('')
                    expect(files).to_have_count(1)
                    form.locator('[name="status"]').select_option('')
                    expect(files).to_have_count(6)
                    expect(metric).to_have_text('6')
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    viewport_evidence(page, 'dev17-import-history-filtered')
                    assert not errors and not writes, (errors, writes)
                    browser.close()
                after = {path: client.get(path).json()["body"]["total"] for path in before}
                assert before == after
                print("PASS actual import history all/source/status/AND/zero/clear/background/narrow; zero UI writes")
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            target_database.engine.dispose()


if __name__ == '__main__':
    run()
