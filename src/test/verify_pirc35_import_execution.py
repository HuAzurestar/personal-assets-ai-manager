"""One real informed approval commits 2500 fictional rows as 1000/1000/500."""
import os
from pathlib import Path
import socket
import sqlite3
import tempfile
import threading
import time

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright
from serve_m2_ui import prepare_app
from browser_artifact import viewport_evidence


def run():
    with tempfile.TemporaryDirectory(prefix="paam-pirc35-import-execution-") as temporary:
        app = prepare_app(Path(temporary))
        from backend.core import target_database
        from backend.entity.transaction_fact import TransactionFact
        from backend.entity.ledger_entry import LedgerEntry
        from backend.entity.review_case import ReviewCase
        from backend.entity.review_allocation import ReviewAllocation
        from backend.entity.transaction_import_row import TransactionImportRow
        from sqlalchemy import select, func
        def counts():
            with target_database.SessionLocal() as db:
                return [db.scalar(select(func.count()).select_from(table)) for table in
                    (TransactionFact, ReviewCase, LedgerEntry, ReviewAllocation, TransactionImportRow)]
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
                    errors, approvals, writes, replies = [], [], [], []
                    page.on("pageerror", lambda error: errors.append(error.stack or str(error)))
                    def track(request):
                        if request.method != "POST":
                            return
                        if request.url.endswith("/operation-approve"):
                            approvals.append(request.post_data_json)
                        elif request.url.endswith(("/operation-confirm", "/confirm")):
                            writes.append((request.url, request.post_data_json))
                    page.on("request", track)
                    page.on("response", lambda response: replies.append(response.json())
                        if response.url.endswith("/operation-confirm") else None)
                    before = counts()
                    page.goto(base + "/#workbench/import")
                    page.locator('[data-action="import-step"][data-step="2"]').last.click()
                    upload = page.locator('[data-form="import-preview"]')
                    header = "建设银行个人交易明细\n账号：990000000000001234\n姓名：Mock测试用户\n币种：人民币\n摘要,币别,交易日期,交易金额,账户余额,交易地点/附言,对方账号与户名\n"
                    content = header + "".join(f"MockSerial{i},CNY,2024-01-01,-{i}.00,10000.00,Mock用途{i},Mock商户\n" for i in range(1, 2501))
                    upload.locator('[name="files"]').set_input_files({"name":"MockSerial2500.csv","mimeType":"text/csv","buffer":content.encode()})
                    upload.locator('[data-action="preview-import"]').click()
                    expect(page.locator('[data-batch-row]')).to_have_count(20, timeout=35000)
                    page.locator('[data-batch-select-scope]').click()
                    expect(page.locator('[data-batch-selection]')).to_contain_text("2500 行", timeout=35000)
                    expect(page.locator('[data-batch-select-scope]')).to_be_enabled(timeout=35000)
                    page.locator('[data-batch-save]').click()
                    expect(page.locator('[data-batch-plan]')).to_be_enabled(timeout=35000)
                    page.locator('[data-batch-plan]').click()
                    expect(page.locator('[data-import-operation]')).to_contain_text("本次明确选择 2500 行", timeout=35000)
                    expect(page.locator('[data-plan-batches] [data-plan-batch]')).to_have_count(3)
                    expect(page.locator('[data-batch-execute]')).to_be_disabled()
                    # Invoking the disabled legacy handler still cannot bypass
                    # the 1000-row financial contract or the whole-plan consent.
                    page.locator('[data-batch-confirm]').evaluate("node=>node.onclick()")
                    page.locator('[data-batch-execute]').evaluate("node=>node.onclick()")
                    assert writes == approvals == [] and counts() == before
                    for width in (1280,820,390):
                        page.set_viewport_size({'width':width,'height':900})
                        page.locator('[data-batch-row]').last.scroll_into_view_if_needed()
                        toolbar=page.locator('[data-batch-toolbar]').bounding_box()
                        assert toolbar and 0 <= toolbar['y'] <= 2 and toolbar['height'] < 300,toolbar
                        topbar=page.locator('.module-topbar').bounding_box()
                        assert topbar and topbar['y']+topbar['height'] <= 2,(width,topbar,toolbar)
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        viewport_evidence(page,f'fix-import-execution-ready-toolbar-{width}')
                    page.set_viewport_size({'width':1280,'height':900})
                    viewport_evidence(page, 'fix-import-execution-2500-approved-plan')
                    page.locator('[data-batch-consent]').check()
                    expect(page.locator('[data-batch-execute]')).to_be_enabled()
                    page.locator('[data-batch-execute]').click()
                    try:
                        expect(page.locator('[data-batch-execution]')).to_contain_text("全部完成", timeout=90000)
                    except Exception:
                        # Bounded diagnostics from this fictional database;
                        # retain the original completion/deadline assertion.
                        print('serial status:', page.locator('[data-batch-status]').inner_text(), flush=True)
                        print('serial page errors:', errors, flush=True)
                        print('serial sent children:', [body['batch_index'] for _, body in writes], flush=True)
                        print('serial replies:', [dict(code=reply.get('body', {}).get('code'),
                            next=reply.get('body', {}).get('next_batch_index')) for reply in replies], flush=True)
                        raise
                    expect(page.locator('[data-batch-selection]')).to_contain_text("0 行", timeout=15000)
                    assert len(approvals) == 1 and approvals[0]['selected_rows'] and len(approvals[0]['selected_rows']) == 2500
                    assert [len(body['selected_rows']) for _, body in writes] == [1000, 1000, 500], page.locator('[data-batch-status]').inner_text()
                    assert all(url.endswith('/operation-confirm') for url, _ in writes)
                    assert [body['batch_index'] for _, body in writes] == [0, 1, 2]
                    results = [reply['body'] for reply in replies]
                    assert len(results) == 3 and [value['complete'] for value in results] == [False, False, True]
                    assert [value['new_fact_count'] for value in results] == [1000, 1000, 500]
                    assert [value['remaining_count'] for value in results] == [1500, 500, 0]
                    assert [writes[i][1]['expected_updated_time'] for i in (1, 2)] == [results[i]['preview_updated_time'] for i in (0, 1)]
                    assert [after - original for after, original in zip(counts(), before)] == [2500] * 5
                    with target_database.SessionLocal() as db:
                        facts = db.scalars(select(TransactionFact).where(TransactionFact.summary.startswith('MockSerial'))).all()
                        ids = [fact.id for fact in facts]
                        allocations = db.scalars(select(ReviewAllocation).where(ReviewAllocation.transaction_id.in_(ids))).all()
                        outputs = db.scalars(select(LedgerEntry).where(LedgerEntry.id.in_([row.ledger_entry_id for row in allocations]))).all()
                        assert len(facts) == len(allocations) == len(outputs) == 2500
                        assert sorted(fact.amount for fact in facts) == list(range(100, 250001, 100))
                        assert sorted(row.amount for row in outputs) == list(range(100, 250001, 100))
                        assert len({row.account_ref_id for row in outputs}) == 1 and outputs[0].account_ref_id > 0
                    assert page.evaluate("localStorage.getItem('paam.import.pending.v1')") is None
                    assert page.evaluate("localStorage.getItem('paam.import.remaining.v1')") is None
                    page.locator('[data-batch-execute]').evaluate("node=>node.onclick()")
                    assert len(writes) == 3 and len(approvals) == 1 and not errors, errors
                    viewport_evidence(page, 'fix-import-execution-2500-complete')
                    # Stop after the server committed child one but while its
                    # successful HTTP response is still held. No child two is
                    # dispatched; completing the last row needs a fresh plan.
                    def next_plan(name, offset):
                        page.locator('[data-action="import-step"][data-step="2"]:visible').first.click()
                        upload = page.locator('[data-form="import-preview"]')
                        text = header + ''.join(f'{name}{i},CNY,2024-01-01,-{i+offset}.00,10000.00,Mock用途{i+offset},Mock商户\n' for i in range(1,1002))
                        upload.locator('[name="files"]').set_input_files({'name':name+'.csv','mimeType':'text/csv','buffer':text.encode()})
                        upload.locator('[data-action="preview-import"]').click()
                        expect(page.locator('[data-batch-row]')).to_have_count(20,timeout=35000)
                        page.locator('[data-batch-select-scope]').click()
                        expect(page.locator('[data-batch-selection]')).to_contain_text('1001 行',timeout=35000)
                        expect(page.locator('[data-batch-select-scope]')).to_be_enabled(timeout=35000)
                        page.locator('[data-batch-save]').click()
                        expect(page.locator('[data-batch-plan]')).to_be_enabled(timeout=35000)
                        page.locator('[data-batch-plan]').click()
                        expect(page.locator('[data-plan-batches] [data-plan-batch]')).to_have_count(2,timeout=35000)
                        page.locator('[data-batch-consent]').check()
                    next_plan('MockStop',3000)
                    held=[]
                    def hold_success(route):
                        if route.request.post_data_json['batch_index'] == 0:
                            response=route.fetch(timeout=35000)
                            assert response.status == 200, response.text()
                            held.append((route,response))
                            page.evaluate('window.__mockSerialResponseHeld=true')
                        else:
                            route.continue_()
                    pattern='**/paam/import/v1/preview/*/operation-confirm'
                    page.route(pattern,hold_success)
                    page.locator('[data-batch-execute]').click()
                    page.wait_for_function("localStorage.getItem('paam.import.pending.v1') !== null")
                    page.wait_for_function('window.__mockSerialResponseHeld === true',timeout=35000)
                    expect(page.locator('[data-batch-stop-execution]')).to_be_enabled()
                    # A Playwright callback pumps while this browser evaluation
                    # waits; the actual finance response must have been held.
                    expect(page.locator('[data-batch-execution]')).to_contain_text('本批正在提交')
                    page.locator('[data-batch-stop-execution]').click()
                    assert held
                    for route,response in held:
                        route.fulfill(response=response)
                    page.unroute(pattern,hold_success)
                    expect(page.locator('[data-batch-execution]')).to_contain_text('后续已停止',timeout=35000)
                    expect(page.locator('[data-batch-selection]')).to_contain_text('1 行',timeout=35000)
                    expect(page.locator('[data-batch-save]')).to_be_enabled(timeout=35000)
                    assert len(writes)==4 and len(approvals)==2
                    assert page.evaluate("JSON.parse(localStorage.getItem('paam.import.remaining.v1')).choices.length") == 1
                    assert page.evaluate("localStorage.getItem('paam.import.pending.v1')") is None
                    viewport_evidence(page,'fix-import-execution-stopped-remaining-one')
                    page.locator('[data-batch-execute]').evaluate('node=>node.onclick()')
                    assert len(writes)==4
                    page.locator('[data-batch-save]').click()
                    expect(page.locator('[data-batch-plan]')).to_be_enabled(timeout=35000)
                    page.locator('[data-batch-plan]').click()
                    expect(page.locator('[data-plan-batches] [data-plan-batch]')).to_have_count(1,timeout=35000)
                    expect(page.locator('[data-batch-execute]')).to_be_disabled()
                    page.locator('[data-batch-consent]').check()
                    page.locator('[data-batch-execute]').click()
                    expect(page.locator('[data-batch-selection]')).to_contain_text('0 行',timeout=35000)
                    expect(page.locator('[data-batch-refresh]')).to_be_enabled(timeout=35000)
                    assert len(writes)==5 and len(approvals)==3 and len(writes[-1][1]['selected_rows'])==1

                    next_plan('MockUnknown',5000)
                    def lose_success(route):
                        response=route.fetch(timeout=35000)
                        assert response.status==200,response.text()
                        route.abort('failed')
                    page.route(pattern,lose_success)
                    page.locator('[data-batch-execute]').click()
                    expect(page.locator('[data-batch-status]')).to_contain_text('提交结果尚待核对',timeout=35000)
                    page.unroute(pattern,lose_success)
                    assert len(writes)==6 and len(approvals)==4
                    assert page.evaluate("JSON.parse(localStorage.getItem('paam.import.pending.v1')).rows.length") == 1000
                    assert page.evaluate("JSON.parse(localStorage.getItem('paam.import.remaining.v1')).choices.length") == 1001
                    page.locator('[data-batch-execute]').evaluate('node=>node.onclick()')
                    assert len(writes)==6
                    page.locator('[data-batch-verify]').click()
                    expect(page.locator('[data-batch-observed]')).to_be_enabled(timeout=35000)
                    page.locator('[data-batch-observed]').click()
                    expect(page.locator('[data-batch-selection]')).to_contain_text('1 行',timeout=35000)
                    expect(page.locator('[data-batch-save]')).to_be_enabled(timeout=35000)
                    assert len(writes)==6 and len(approvals)==4
                    expect(page.locator('[data-batch-execution]')).to_contain_text('尚待处理 1 行')
                    expect(page.locator('[data-batch-execution]')).not_to_contain_text('本批结果未知')
                    assert page.evaluate("localStorage.getItem('paam.import.pending.v1')") is None
                    assert page.evaluate("JSON.parse(localStorage.getItem('paam.import.remaining.v1')).choices.length") == 1
                    viewport_evidence(page,'fix-import-execution-unknown-reconciled-remaining-one')
                    # Route remount retains only a draft. It performs neither
                    # another financial POST nor an implicit new approval.
                    page.goto(base+'/#details/transaction-fact')
                    page.goto(base+'/#workbench/import')
                    assert len(writes)==6 and len(approvals)==4 and not errors, errors
                    page.close()
                    # These are actual backend failures, not invented HTTP
                    # responses: change the frozen source metadata via its
                    # public API or hold a separate SQLite writer after child
                    # one commits but before its response reaches the browser.
                    # Fresh browser contexts keep the reconciled unknown draft
                    # above intact; all databases remain purely fictional.
                    for code, offset in [('STALE_PREVIEW',7000),('WRITE_BUSY',9000)]:
                        page=browser.new_page(viewport={'width':1280,'height':900})
                        page.on('pageerror',lambda error: errors.append(error.stack or str(error)))
                        page.on('request',track)
                        page.goto(base+'/#workbench/import')
                        next_plan('Mock'+code,offset)
                        original=counts()
                        write_start, approval_start=len(writes),len(approvals)
                        failed_responses, locks, frozen=[] ,[],[]
                        def database_snapshot():
                            with target_database.SessionLocal() as db:
                                return {name:[dict(row) for row in db.execute(select(table).order_by(table.c.id)).mappings()]
                                    for name,table in target_database.TargetBase.metadata.tables.items()}
                        def fail_second_child(route):
                            response=route.fetch(timeout=35000)
                            if route.request.post_data_json['batch_index']==0:
                                assert response.status==200,response.text()
                                assert response.json()['body']['new_fact_count']==1000
                                if code=='STALE_PREVIEW':
                                    refs=client.get('/paam/ledger/v1/account-ref/list',params={'page_size':100}).json()['body']['items']
                                    assert len(refs)==1,refs
                                    ref=refs[0]
                                    changed=client.put(f"/paam/ledger/v1/account-ref/{ref['id']}/metadata",json={
                                        'account_id':ref['account_id'],'name':'Mock changed between children',
                                        'institution':ref['institution'],'reference':ref['reference'],
                                        'status':ref['status'],'expected_updated_time':ref['updated_time']})
                                    assert changed.status_code==200,changed.text
                                else:
                                    database=Path(target_database.engine.url.database).resolve()
                                    assert database.is_relative_to(Path(temporary).resolve()),database
                                    lock=sqlite3.connect(database,isolation_level=None)
                                    lock.execute('BEGIN IMMEDIATE')
                                    locks.append(lock)
                                frozen.append(database_snapshot())
                            else:
                                assert response.status==(409 if code=='STALE_PREVIEW' else 503),response.text()
                                assert response.json()['body']['code']==code,response.text()
                                failed_responses.append(response.json())
                                assert database_snapshot()==frozen[0],code
                            route.fulfill(response=response)
                        page.route(pattern,fail_second_child)
                        try:
                            page.locator('[data-batch-execute]').click()
                            expect(page.locator('[data-batch-status]')).to_contain_text(code,timeout=35000)
                            expect(page.locator('[data-batch-status]')).to_contain_text('原计划已失效' if code=='STALE_PREVIEW' else '数据库正忙')
                            for text in ('本批未提交','保存选择并重新核验','再次明确批准','不会自动重发'):
                                expect(page.locator('[data-batch-status]')).to_contain_text(text)
                            expect(page.locator('[data-batch-execution]')).to_contain_text('后续已停止',timeout=35000)
                            expect(page.locator('[data-batch-execution]')).to_contain_text('1000 行')
                            expect(page.locator('[data-batch-execution]')).not_to_contain_text('本批结果未知')
                            expect(page.locator('[data-batch-selection]')).to_contain_text('1 行',timeout=35000)
                            expect(page.locator('[data-batch-selection]')).to_contain_text('已保存')
                            expect(page.locator('[data-batch-selection]')).not_to_contain_text('未保存更改')
                            expect(page.locator('[data-batch-status]')).to_contain_text('核验导入')
                            expect(page.locator('[data-batch-save]')).to_be_enabled(timeout=35000)
                        finally:
                            for lock in locks:
                                lock.rollback()
                                lock.close()
                            page.unroute(pattern,fail_second_child)
                        assert len(failed_responses)==1
                        assert [len(body['selected_rows']) for _,body in writes[write_start:]]==[1000,1]
                        assert len(approvals)==approval_start+1
                        assert [after-before for after,before in zip(counts(),original)]==[1000]*5
                        assert page.evaluate("localStorage.getItem('paam.import.pending.v1')") is None
                        assert page.evaluate("JSON.parse(localStorage.getItem('paam.import.remaining.v1')).choices.length")==1
                        viewport_evidence(page,'fix-import-execution-known-'+code.lower()+'-remaining-one')
                        page.locator('[data-batch-execute]').evaluate('node=>node.onclick()')
                        assert len(writes)==write_start+2 and len(approvals)==approval_start+1
                        # A rejected child is known not committed. Its remaining
                        # draft must be re-read/saved and explicitly approved,
                        # never replayed under the old whole-plan approval.
                        page.locator('[data-batch-save]').click()
                        expect(page.locator('[data-batch-plan]')).to_be_enabled(timeout=35000)
                        page.locator('[data-batch-plan]').click()
                        expect(page.locator('[data-plan-batches] [data-plan-batch]')).to_have_count(1,timeout=35000)
                        expect(page.locator('[data-batch-execute]')).to_be_disabled()
                        page.locator('[data-batch-execute]').evaluate('node=>node.onclick()')
                        assert len(writes)==write_start+2 and len(approvals)==approval_start+1
                        page.locator('[data-batch-consent]').check()
                        page.locator('[data-batch-execute]').click()
                        expect(page.locator('[data-batch-execution]')).to_contain_text('全部完成',timeout=35000)
                        expect(page.locator('[data-batch-selection]')).to_contain_text('0 行',timeout=35000)
                        expect(page.locator('[data-batch-refresh]')).to_be_enabled(timeout=35000)
                        assert [len(body['selected_rows']) for _,body in writes[write_start:]]==[1000,1,1]
                        assert len(approvals)==approval_start+2
                        assert [after-before for after,before in zip(counts(),original)]==[1001]*5
                        assert page.evaluate("localStorage.getItem('paam.import.remaining.v1')") is None
                        assert page.evaluate("localStorage.getItem('paam.import.pending.v1')") is None
                        assert not errors,errors
                        page.close()
                    browser.close()
            print('PASS one informed real UI approval, exact 2500 actual 1000/1000/500, committed-inflight stop retains one unsubmitted row, explicit remaining reapproval, committed-lost response readonly reconciliation retains one row, actual stale metadata and busy SQLite failures preserve prior1000 and remaining1, explicit fresh approval, no replay or provider calls')
        finally:
            server.should_exit = True
            worker.join(timeout=5)
            target_database.engine.dispose()


if __name__ == "__main__":
    run()
    from import_restart_browser import run_import_restart
    run_import_restart()
