"""Actual explicit whole keyless repeat-export draft; fresh fictional SQLite."""
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
    with tempfile.TemporaryDirectory(prefix="paam-repeat-draft-") as temporary:
        app = prepare_app(Path(temporary))
        from backend.core import target_database
        from sqlalchemy import select
        from backend.entity import TransactionFact, ReviewCase, LedgerEntry, ReviewAllocation, TransactionImportRow
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",0)); port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=port,log_level="error"))
        worker = threading.Thread(target=server.run,daemon=True); worker.start()
        try:
            def snapshot():
                with target_database.SessionLocal() as db:
                    return {table.name:tuple(tuple(row) for row in db.execute(select(table).order_by(table.c.id)))
                        for table in target_database.TargetBase.metadata.sorted_tables}

            with httpx.Client(base_url=base,trust_env=False,timeout=35) as client:
                for _ in range(100):
                    try:
                        if client.get("/api/health").status_code == 200: break
                    except httpx.HTTPError: pass
                    time.sleep(.1)
                else: raise RuntimeError("fictional app did not start")
                content = (Path(__file__).parent / "fixtures/pirc35/ccb-2.csv").read_text(encoding="utf-8").replace("2024-","2048-").encode()
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else None,headless=True)
                    page = browser.new_page(viewport={"width":1280,"height":800})
                    errors,confirmations,repeat_reads = [],[],[]
                    page.on("pageerror",lambda error:errors.append(str(error)))
                    page.on("request",lambda request:confirmations.append(request.post_data_json)
                        if request.method=="POST" and request.url.endswith("/confirm") else None)
                    page.on("request",lambda request:repeat_reads.append(request.post_data_json)
                        if request.method=="POST" and request.url.endswith("/repeat-preview") else None)
                    page.goto(base+"/#workbench/import")
                    page.locator('[data-action="import-step"][data-step="2"]').first.click()
                    upload = page.locator('[data-form="import-preview"]')
                    upload.locator('[name="files"]').set_input_files([dict(name=f"Mock repeat {n}.csv",mimeType="text/csv",
                        buffer=content+b"\n"*n) for n in (0,1)])
                    with page.expect_response("**/paam/import/v1/preview") as response:
                        upload.locator('[data-action="preview-import"]').click()
                    assert response.value.status == 200,response.value.text()
                    current = response.value.json()["body"]
                    first,second = sorted(file["file_id"] for file in current["files"])
                    rows = page.locator('[data-batch-row]')
                    expect(rows).to_have_count(20,timeout=15000)
                    expect(rows.first.locator('[data-row-decision]')).to_have_value("SKIP")
                    before = snapshot()
                    # The ordinary entry is available without opening advanced.
                    assert not page.locator('[data-batch-advanced]').evaluate("node=>node.open")
                    page.locator('[data-batch-file]').select_option(str(second))
                    expect(page.locator('[data-batch-repeat]')).to_be_enabled(timeout=15000)
                    page.locator('[data-batch-repeat]').click()
                    modal = page.locator('dialog:visible').last
                    expect(modal.locator('[data-repeat-status]')).to_contain_text("完整核对 24 行",timeout=35000)
                    expect(modal.locator('[data-repeat-count]')).to_contain_text("建议 0 组")
                    expect(modal.locator('[data-repeat-apply]')).to_be_disabled()
                    modal.locator('summary').filter(has_text="未纳入建议组").click()
                    expect(modal.locator('[data-repeat-exceptions]')).to_contain_text("筛选未包含完整组")
                    assert snapshot()==before and not confirmations
                    modal.locator('[data-workbench-close]').click()
                    page.locator('[data-batch-file]').select_option(value=[""])
                    expect(page.locator('[data-batch-repeat]')).to_be_enabled(timeout=15000)
                    def failed(route):
                        route.fulfill(status=503,content_type="application/json",body='{"status":503,"message":"Mock read unavailable","body":{"code":"MOCK_READ_FAILED"}}')
                    page.route("**/repeat-preview",failed,times=1)
                    page.locator('[data-batch-repeat]').click()
                    modal = page.locator('dialog:visible').last
                    expect(modal.locator('[data-repeat-status]')).to_contain_text("MOCK_READ_FAILED",timeout=35000)
                    expect(modal.locator('[data-repeat-apply]')).to_be_disabled()
                    assert snapshot()==before and not confirmations
                    modal.locator('[data-repeat-read]').click()
                    expect(modal.locator('[data-repeat-status]')).to_contain_text("完整核对 48 行",timeout=35000)
                    expect(modal.locator('[data-repeat-count]')).to_contain_text("建议 24 组；已选 0 组")
                    assert modal.locator('[data-repeat-group]').count()==20
                    expect(modal.locator('[data-repeat-apply]')).to_be_disabled()
                    modal.locator('[data-repeat-all]').click()
                    expect(modal.locator('[data-repeat-count]')).to_contain_text("接受 24 行，跳过 24 行")
                    expect(modal.locator('[data-repeat-apply]')).to_be_disabled()
                    modal.locator('[data-repeat-ack]').check()
                    expect(modal.locator('[data-repeat-apply]')).to_be_enabled()
                    for width in (1280,820,390):
                        page.set_viewport_size(dict(width=width,height=800))
                        modal.locator('[data-repeat-group]').first.scroll_into_view_if_needed()
                        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"),width
                        assert modal.evaluate("node=>node.scrollWidth<=node.clientWidth"),width
                        viewport_evidence(page,f"dev17-repeat-confirm-{width}")
                    page.set_viewport_size(dict(width=1280,height=800))
                    modal.locator('[data-repeat-apply]').click()
                    expect(page.locator('[data-batch-selected-scope]')).to_contain_text("接受 24，跳过 24")
                    expect(page.locator('[data-batch-confirm]')).to_be_disabled()
                    assert snapshot()==before and not confirmations
                    page.locator('[data-batch-save]').click()
                    expect(page.locator('[data-batch-confirm]')).to_be_enabled(timeout=35000)
                    assert snapshot()==before and not confirmations
                    with page.expect_response("**/confirm") as response:
                        page.locator('[data-batch-confirm]').click()
                    assert response.value.status == 200,response.value.text()
                    result = response.value.json()["body"]
                    assert result["new_fact_count"] == result["skipped_count"] == 24
                    assert result["invalid_count"] == result["remaining_count"] == 0
                    assert len(confirmations)==1 and len(confirmations[0]["selected_rows"])==48
                    assert [len(payload["choices"]) for payload in repeat_reads]==[24,48,48]
                    with target_database.SessionLocal() as db:
                        for entity in (TransactionFact,ReviewCase,LedgerEntry,ReviewAllocation):
                            assert len(list(db.execute(select(entity.__table__))))==len(before[entity.__tablename__])+24
                        persisted = list(db.execute(select(TransactionImportRow.__table__).where(
                            TransactionImportRow.transaction_import_file_id.in_([first,second]))).mappings())
                        assert len(persisted)==48 and all(row["raw_payload"] for row in persisted)
                        assert all(row["row_status"]==(1 if row["transaction_import_file_id"]==first else 2) for row in persisted)
                    assert not errors,errors
                    browser.close()
                print("PASS actual full keyless repeat groups, partial filter refusal, readonly failure/retry, explicit first24 NEW/later24 SKIP, compact three widths, one existing financial confirm and immutable Raw")
        finally:
            server.should_exit=True;worker.join(timeout=10);target_database.engine.dispose()


if __name__=="__main__": run()
