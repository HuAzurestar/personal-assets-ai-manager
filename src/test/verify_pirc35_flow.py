"""Actual Edge/browser canonical cash reads, masked search and paged fallback."""
from datetime import datetime, timezone
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
    with tempfile.TemporaryDirectory(prefix="paam-pirc35-flow-") as temporary:
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
            from backend.core import target_database
            from backend.entity import (TransactionFact, LedgerEntry, TargetTagView, TargetTag, LedgerEntryTag,
                ReviewCase, TransactionImportFile, TransactionImportRow, LedgerAccountParty)
            from backend.mapper.review_command_mapper import ReviewCommandMapper
            from sqlalchemy import select, func
            with target_database.SessionLocal() as db:
                first = (db.scalar(select(func.max(TransactionFact.id))) or 0) + 1
                ids = list(range(first, first + 44))
                db.add_all([TransactionFact(id=i, fact_key=f"mock-flow-browser-{i}",
                    occurred_time=datetime(2026, 9, 1, tzinfo=timezone.utc), cash_direction=1 if i % 2 else 2,
                    amount=1000, currency_code="CNY", account_code="1234567890123456", summary=
                        "Mock Café literal %_ 1234567890123456" if i == first else "Mock other") for i in ids])
                db.flush()
                ReviewCommandMapper(db).create_initial_defaults(ids)
                db.get(ReviewCase, first).title = "Mock Café review %_ 1234567890123456"
                file_id = (db.scalar(select(func.max(TransactionImportFile.id))) or 0) + 1
                db.add(TransactionImportFile(id=file_id,filename='Mock source.csv',sha256='f'*64,source_type=101,file_format=1,status=1))
                db.add(TransactionImportRow(transaction_fact_id=first,transaction_import_file_id=file_id,
                    source_row_number=1,source_reference='1234567890123456',raw_payload='{"fictional":"explicit raw only"}',row_status=1))
                db.add_all([LedgerAccountParty(name="Mock sparse person" if i == 44 else f"Mock other person {i}") for i in range(45)])
                db.commit()
                fact_total = db.scalar(select(func.count()).select_from(TransactionFact))
                flow_total = db.scalar(select(func.count()).select_from(LedgerEntry))
                review_total = db.scalar(select(func.count()).select_from(ReviewCase))
                flow_id = db.scalar(select(LedgerEntry.id).where(LedgerEntry.amount == 1000,
                    LedgerEntry.id >= first).order_by(LedgerEntry.id))
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 960})
                errors, writes = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: writes.append(request.url) if request.method not in ("GET", "HEAD") else None)
                page.goto(base + "/#details/ledger?sort_field=id&sort_order=desc&page_size=2&word=CAFÉ%20literal%20%25_&search_field=summary")
                # One user search automatically crosses many zero-hit batches.
                expect(page.locator('[data-flow-scan-status]')).to_contain_text("本次扫描结束", timeout=15000)
                expect(page.locator('[data-flow-scan-status]')).to_contain_text(f"已扫描 {flow_total} 个候选；找到 1 项")
                expect(page.locator('[data-action="economic-detail"]')).to_have_count(1)
                expect(page.locator('[data-flow-continue]')).to_be_disabled()
                viewport_evidence(page, "fix-r20-flow-auto")
                # Exercise the real common picker against 45 public metadata
                # candidates: two empty batches then one named hit, no clicks
                # on batch continuation and no synthetic search response.
                page.evaluate("""async () => {
                    const {mountPicker, metadataLabel} = await import('/static/js/component/workbench.js');
                    const host = document.createElement('section'); host.id = 'scan-picker-test';
                    document.body.append(host); window.pickerAbort = new AbortController();
                    window.pickerChoice = null;
                    await mountPicker(host, {url:'/paam/ledger/v1/account-party', searchKeys:['display_label'],
                        describe:metadataLabel, signal:window.pickerAbort.signal,
                        choose:row => {window.pickerChoice = row.id;}});
                }""")
                picker = page.locator('#scan-picker-test')
                picker.locator('[data-picker-word]').fill('Mock sparse person')
                picker.locator('[data-picker-search]').click()
                expect(picker.locator('[data-picker-scan-status]')).to_contain_text('本次扫描结束')
                expect(picker.locator('[data-picker-scan-status]')).to_contain_text('已扫描 45 个候选；找到 1 项')
                expect(picker.locator('.picker-list-row')).to_have_count(1)
                picker.locator('[data-picker-id]').click()
                assert page.evaluate('window.pickerChoice > 0')
                viewport_evidence(page, 'fix-r20-picker-auto')

                # Fail only a continuation GET; its cursor is retained and a
                # user retry continues serially without restarting batch one.
                failed, search_urls = [], []
                def fail_once(route):
                    search_urls.append(route.request.url)
                    if 'cursor=' in route.request.url and not failed:
                        failed.append(route.request.url)
                        route.fulfill(status=503, content_type='application/json',
                            body='{"status":503,"message":"fictional read failure","body":{"code":"QUERY_TIMEOUT"}}')
                    else:
                        route.continue_()
                page.route('**/paam/ledger/v1/account-party/search?**', fail_once)
                picker.locator('[data-picker-word]').fill('Mock absent')
                picker.locator('[data-picker-search]').click()
                expect(picker.locator('[data-picker-scan-status]')).to_contain_text('读取失败')
                expect(picker.locator('[data-picker-scan-status]')).to_contain_text('已扫描 20 个候选')
                expect(picker.locator('[data-picker-continue]')).to_be_enabled()
                picker.locator('[data-picker-continue]').click()
                expect(picker.locator('[data-picker-scan-status]')).to_contain_text('本次扫描结束')
                expect(picker.locator('[data-picker-scan-status]')).to_contain_text('已扫描 45 个候选；找到 0 项')
                assert search_urls[1] == search_urls[2] == failed[0]
                page.unroute('**/paam/ledger/v1/account-party/search?**', fail_once)

                # Hold a continuation in the actual browser to make pause and
                # cancellation deterministic, then release the aborted read.
                held = []
                def hold_once(route):
                    if 'cursor=' in route.request.url and not held:
                        held.append(route)
                    else:
                        route.continue_()
                page.route('**/paam/ledger/v1/account-party/search?**', hold_once)
                picker.locator('[data-picker-word]').fill('Mock sparse person')
                picker.locator('[data-picker-search]').click()
                expect(picker.locator('[data-picker-pause]')).to_be_enabled()
                # Wait for the intercepted continuation, not merely ready UI.
                for _ in range(100):
                    if held:
                        break
                    page.wait_for_timeout(10)
                assert held
                picker.locator('[data-picker-pause]').click()
                expect(picker.locator('[data-picker-scan-status]')).to_contain_text('已暂停')
                held[0].abort()
                picker.locator('[data-picker-continue]').click()
                expect(picker.locator('[data-picker-scan-status]')).to_contain_text('本次扫描结束')
                expect(picker.locator('.picker-list-row')).to_have_count(1)
                held.clear()
                picker.locator('[data-picker-word]').fill('Mock absent')
                picker.locator('[data-picker-search]').click()
                for _ in range(100):
                    if held:
                        break
                    page.wait_for_timeout(10)
                assert held
                picker.locator('[data-picker-cancel]').click()
                expect(picker.locator('[data-picker-scan-status]')).to_contain_text('已取消自动查找')
                held[0].abort()
                expect(picker.locator('[data-picker-scan-status]')).to_contain_text('已扫描 20 个候选')
                # Editing a cancelled condition starts a fresh scan; it does
                # not inherit its cursor, disabled buttons or previous hits.
                picker.locator('[data-picker-word]').fill('Mock sparse person')
                picker.locator('[data-picker-search]').click()
                expect(picker.locator('[data-picker-scan-status]')).to_contain_text('本次扫描结束')
                expect(picker.locator('[data-picker-scan-status]')).to_contain_text('已扫描 45 个候选；找到 1 项')
                page.unroute('**/paam/ledger/v1/account-party/search?**', hold_once)
                page.evaluate("window.pickerAbort.abort(); document.getElementById('scan-picker-test').remove()")
                page.locator('[data-action="economic-detail"]').click()
                drawer = page.locator('.inspection-workspace[open]')
                expect(drawer).to_contain_text("Mock Café literal %_ ****3456")
                expect(drawer).to_contain_text("来源未识别（ref = 0）")
                expect(drawer).to_contain_text("现金到数量腿归因（不是额外现金）")
                assert "1234567890123456" not in drawer.inner_text()
                drawer.locator('[data-close]').click()
                page.locator('[data-form="economic-filter"] [name="word"]').fill("")
                page.locator('[data-form="economic-filter"] [name="sort"]').select_option("signed_cash_amount.asc")
                expect(page.locator('[data-action="economic-detail"]')).to_have_count(2)
                expect(page.locator('[data-form="economic-filter"]')).to_contain_text("带方向金额")
                # Add many archived relation rows in the isolated test DB only.
                with target_database.SessionLocal() as db:
                    view_id = (db.scalar(select(func.max(TargetTagView.id))) or 0) + 1
                    tag_start = (db.scalar(select(func.max(TargetTag.id))) or 0) + 1
                    db.add(TargetTagView(id=view_id, name="Mock archived view", system_name="mock_archived_view", status="ARCHIVED"))
                    db.add_all([TargetTag(id=i, view_id=view_id, name=f"Mock archived {i}", system_name=f"mock_archived_{i}", status="ARCHIVED") for i in range(tag_start, tag_start + 4000)])
                    db.add_all([LedgerEntryTag(ledger_id=flow_id, tag_id=i) for i in range(tag_start, tag_start + 4000)])
                    db.commit()
                page.goto(base + f"/#details/ledger?sort_field=id&sort_order=asc&account_ref_id=0")
                page.locator(f'[data-action="economic-detail"][data-id="{flow_id}"]').click()
                expect(drawer).to_contain_text("完整详情超出预算")
                tags = drawer.locator('[data-flow-relation="tag"]')
                expect(tags.locator('[data-rel-status]')).to_contain_text("第 1 页（仅本页）")
                assert len(tags.locator('.inspection-flow').all()) == 20
                tags.locator('[data-rel-next]').click()
                expect(tags.locator('[data-rel-status]')).to_contain_text("第 2 页（仅本页）")
                tags.locator('[data-rel-prev]').click()
                expect(tags.locator('[data-rel-status]')).to_contain_text("第 1 页（仅本页）")
                drawer.locator('[data-close]').click()
                page.goto(base + "/#details/review?sort_field=id&sort_order=desc&page_size=2&word=CAFÉ%20review%20%25_")
                expect(page.locator('[data-review-scan-status]')).to_contain_text("本次扫描结束", timeout=15000)
                expect(page.locator('[data-review-scan-status]')).to_contain_text(f"已扫描 {review_total} 个候选；找到 1 项")
                page.locator('[data-action="economic-review-detail"]').click()
                expect(drawer).to_contain_text("Mock Café review %_ ****3456")
                drawer.locator('[data-close]').click()
                form = page.locator('[data-form="detail-review-filter"]')
                expect(form.locator('[name="type"] option')).to_have_count(6)
                form.locator('[name="word"]').fill('')
                form.locator('[name="type"]').select_option('NORMAL_TRANSACTION')
                expect(page.locator('[data-action="economic-review-detail"]')).to_have_count(2)
                page.goto(base + '/#details/transaction-fact?sort_field=id&sort_order=desc&page_size=2&word=CAFÉ%20literal%20%25_')
                expect(page.locator('[data-fact-scan-status]')).to_contain_text('本次扫描结束', timeout=15000)
                expect(page.locator('[data-fact-scan-status]')).to_contain_text(f'已扫描 {fact_total} 个候选；找到 1 项')
                page.locator('[data-action="fact-detail"]').click()
                expect(drawer).to_contain_text('尚未解释')
                assert '1234567890123456' not in drawer.inner_text()
                assert 'explicit raw only' not in drawer.inner_text()
                drawer.locator('[data-fact-source-evidence]').click()
                raw = page.locator('dialog[open]').last
                expect(raw).to_contain_text('explicit raw only')
                raw.locator('[data-workbench-close]').click()
                drawer.locator('[data-close]').click()
                form = page.locator('[data-form="fact-filter"]')
                form.locator('[name="word"]').fill('')
                form.locator('[name="sort"]').select_option('signed_amount.asc')
                expect(page.locator('[data-action="fact-detail"]')).to_have_count(2)
                # Full source history is never truncated into a fake complete detail.
                from sqlalchemy import text
                with target_database.SessionLocal() as db:
                    db.execute(text("""WITH RECURSIVE seq(i) AS (VALUES(2) UNION ALL SELECT i+1 FROM seq WHERE i<4001)
                        INSERT INTO transaction_import_row(transaction_fact_id,transaction_import_file_id,source_row_number,
                          source_reference,raw_payload,raw_hash,row_status,issue_code,issue_message,created_time,updated_time)
                        SELECT :fid,:file_id,i,'mock-'||i,'{"fictional":"explicit raw only"}','','1','','',
                          '2026-09-01T00:00:00.000000Z','2026-09-01T00:00:00.000000Z' FROM seq"""),dict(fid=first,file_id=file_id))
                    db.commit()
                page.goto(base + '/#details/transaction-fact?sort_field=id&sort_order=asc')
                page.locator(f'[data-action="fact-detail"][data-id="{first}"]').click()
                expect(drawer).to_contain_text('完整 Fact 详情超出预算')
                sources = drawer.locator('[data-fact-relation="source_row"]')
                expect(sources.locator('[data-rel-status]')).to_contain_text('共 4001 项 · 第 1 页')
                sources.locator('[data-rel-next]').click()
                expect(sources.locator('[data-rel-status]')).to_contain_text('第 2 页（仅本页）')
                assert len(sources.locator('[data-fact-source-evidence]').all()) == 20
                sources.locator('[data-rel-prev]').click()
                expect(sources.locator('[data-rel-status]')).to_contain_text('第 1 页（仅本页）')
                drawer.locator('[data-close]').click()
                page.goto(base + '/#overview?month=2026-09&currency_code=CNY')
                expect(page.locator('[data-action="account-type"]').first).to_have_attribute('data-value','TRANSACTION')
                expect(page.locator('.account-note')).to_contain_text('DUPLICATE 仅留证据，不计金额')
                page.locator('[data-action="account-metric"][data-value="INCOME"]').click()
                expect(page.locator('[data-form="economic-filter"] [name="cash_direction"]')).to_have_value('IN')
                expect(page.locator('[data-form="economic-filter"] [name="cash_currency_code"]')).to_have_value('CNY')
                expect(page.locator('[data-form="economic-filter"] [name="active"]')).to_have_value('true')
                assert page.locator('.fact-amount.outflow').count() == 0
                # Legacy large original Review fixture: complete relationships,
                # no invented system defaults or actual application writes.
                from sqlalchemy import text
                with target_database.SessionLocal() as db:
                    rid = (db.scalar(select(func.max(ReviewCase.id))) or 0) + 1
                    fstart = (db.scalar(select(func.max(TransactionFact.id))) or 0)
                    lstart = (db.scalar(select(func.max(LedgerEntry.id))) or 0)
                    db.add(ReviewCase(id=rid, behavior_type=4, status=0, title='Mock large original Review'))
                    db.flush()
                    params = dict(rid=rid,fstart=fstart,lstart=lstart)
                    db.execute(text("""WITH RECURSIVE seq(i) AS (VALUES(1) UNION ALL SELECT i+1 FROM seq WHERE i<2000)
                        INSERT INTO transaction_fact(id,fact_key,occurred_time,cash_direction,amount,currency_code,
                          account_code,counterparty_name,counterparty_account_ref,summary,created_time,updated_time)
                        SELECT :fstart+i,'mock-big-'||i,'2026-09-01T00:00:00.000000Z',1,1,'CNY','','Mock','','Mock big',
                          '2026-09-01T00:00:00.000000Z','2026-09-01T00:00:00.000000Z' FROM seq"""), params)
                    db.execute(text("""INSERT INTO ledger_entry(id,entry_type,entry_direction,cash_amount,cash_currency_code,
                        account_ref_id,account_code,counterparty_account_ref,occurred_time,created_time,updated_time)
                        SELECT :lstart+id-:fstart,0,1,1,'CNY',0,'','',occurred_time,created_time,updated_time
                        FROM transaction_fact WHERE id>:fstart"""), params)
                    db.execute(text("""INSERT INTO review_transaction_ledger_allocation(review_id,transaction_id,ledger_id,
                        cash_amount,cash_currency_code,created_time,updated_time)
                        SELECT :rid,id,:lstart+id-:fstart,1,'CNY',created_time,updated_time FROM transaction_fact WHERE id>:fstart"""), params)
                    db.commit()
                page.goto(base + '/#details/review?type=OTHER_MANUAL&sort_field=id&sort_order=desc')
                page.locator(f'[data-action="economic-review-detail"][data-id="{rid}"]').click()
                expect(drawer).to_contain_text('各关系独立分页')
                allocations = drawer.locator('[data-review-relation="allocation"]')
                expect(allocations.locator('[data-rel-status]')).to_contain_text('共 2000 项 · 第 1 页')
                allocations.locator('[data-rel-next]').click()
                expect(allocations.locator('[data-rel-status]')).to_contain_text('第 2 页（仅本页）')
                flows = drawer.locator('[data-review-relation="flow"]')
                expect(flows.locator('[data-rel-status]')).to_contain_text('共 2000 项 · 第 1 页')
                assert not errors, errors
                assert not writes, writes
                browser.close()
                print("PASS canonical Flow/Review/Fact UI: serial automatic empty-batch search, masking, five Review types, grouped money sort, explicit single-row raw evidence and complete paged fallbacks; zero HTTP writes/providers")
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            from backend.core import target_database
            target_database.engine.dispose()


if __name__ == "__main__":
    run()
