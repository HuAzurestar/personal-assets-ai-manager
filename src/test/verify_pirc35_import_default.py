"""Actual upload of two fictional reliable exports; keep first, skip later."""
import json
import os
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
    with tempfile.TemporaryDirectory(prefix="paam-certain-default-") as temporary:
        app = prepare_app(Path(temporary))
        from backend.core import target_database
        from sqlalchemy import select
        from backend.entity import TransactionFact, ReviewCase, LedgerEntry, ReviewAllocation, TransactionImportRow
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        worker = threading.Thread(target=server.run, daemon=True); worker.start()
        try:
            def snapshot():
                with target_database.SessionLocal() as db:
                    return {table.name:tuple(tuple(row) for row in db.execute(select(table).order_by(table.c.id)))
                        for table in target_database.TargetBase.metadata.sorted_tables}

            with httpx.Client(base_url=base, trust_env=False, timeout=35) as client:
                for _ in range(100):
                    try:
                        if client.get("/api/health").status_code == 200: break
                    except httpx.HTTPError: pass
                    time.sleep(.1)
                else: raise RuntimeError("fictional app did not start")
                content = (Path(__file__).parent / "fixtures/pirc35/alipay-6.csv").read_text(encoding="utf-8").replace("2024-", "2045-").encode()
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                    page = browser.new_page(viewport={"width":1280, "height":800})
                    errors, confirmations = [], []
                    page.on("pageerror", lambda error:errors.append(str(error)))
                    page.on("request", lambda request:confirmations.append(request.post_data_json)
                        if request.method == "POST" and request.url.endswith("/confirm") else None)
                    page.goto(base + "/#workbench/import")
                    page.locator('[data-action="import-step"][data-step="2"]').first.click()
                    upload = page.locator('[data-form="import-preview"]')
                    upload.locator('[name="files"]').set_input_files([
                        dict(name=f"Mock export {n}.csv", mimeType="text/csv", buffer=content+b"\n"*n) for n in (0,1)])
                    with page.expect_response("**/paam/import/v1/preview") as response:
                        upload.locator('[data-action="preview-import"]').click()
                    assert response.value.status == 200, response.value.text()
                    current = response.value.json()["body"]
                    first, second = [item["file_id"] for item in current["files"]]
                    rows = page.locator('[data-batch-row]')
                    expect(rows).to_have_count(20, timeout=15000)
                    expect(rows.first.locator('[data-row-decision]')).to_have_value("ACCEPT")
                    expect(rows.first.locator('[data-row-certain]')).to_contain_text("本组首份")
                    before = snapshot()
                    page.locator('[data-batch-file]').select_option(str(second))
                    expect(rows.first.locator('[data-row-certain]')).to_contain_text(f"首份为文件 #{first}")
                    assert rows.locator('[data-row-decision]').evaluate_all("nodes => nodes.every(node => node.value === 'SKIP')")
                    # Filtering cannot promote the later export to a new first.
                    params = dict(preview_digest=current["preview_digest"], page_size=100,
                        filter=json.dumps(dict(key="file_id", op="=", val=second)))
                    actual = client.get(f'/paam/import/v1/preview/{current["token"]}/row/list', params=params)
                    assert actual.status_code == 200, actual.text
                    assert all(item["default_decision"] == "SKIP" for item in actual.json()["body"]["items"])
                    for width in (1280, 820, 390):
                        page.set_viewport_size(dict(width=width, height=800)); rows.first.scroll_into_view_if_needed()
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
                        viewport_evidence(page, f"dev17-certain-later-{width}")
                    page.set_viewport_size(dict(width=1280, height=800))
                    page.locator('[data-batch-file]').select_option(value=[""])
                    expect(rows.first.locator('[data-row-decision]')).to_have_value("ACCEPT")
                    assert not confirmations and snapshot() == before
                    page.locator('[data-batch-guide]').click()
                    expect(page.locator('[data-batch-confirm]')).to_be_enabled(timeout=35000)
                    expect(page.locator('[data-batch-selected-scope]')).to_contain_text("接受 24，跳过 24")
                    assert not confirmations and snapshot() == before
                    with page.expect_response("**/confirm") as response:
                        page.locator('[data-batch-confirm]').click()
                    result = response.value.json()["body"]
                    assert result["new_fact_count"] == result["skipped_count"] == 24
                    assert result["remaining_count"] == result["invalid_count"] == 0
                    assert len(confirmations) == 1 and len(confirmations[0]["selected_rows"]) == 48
                    with target_database.SessionLocal() as db:
                        for entity in (TransactionFact, ReviewCase, LedgerEntry, ReviewAllocation):
                            current_rows = list(db.execute(select(entity.__table__)).mappings())
                            assert len(current_rows) == len(before[entity.__tablename__]) + 24
                        persisted = list(db.execute(select(TransactionImportRow.__table__).where(
                            TransactionImportRow.transaction_import_file_id.in_([first,second]))).mappings())
                        assert len(persisted) == 48 and all(item["raw_payload"] for item in persisted)
                        assert all(item["row_status"] == (1 if item["transaction_import_file_id"] == first else 2) for item in persisted)
                    assert not errors, errors
                    browser.close()
                print("PASS actual two reliable exports default first24 accept/later24 skip, filtered scope, compact viewports and one immutable financial confirmation")
        finally:
            server.should_exit = True; worker.join(timeout=10); target_database.engine.dispose()


if __name__ == "__main__": run()
