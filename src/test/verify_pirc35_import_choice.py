"""Visible manual import intents and real atomic writes on fictional CSV only."""
import base64
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
from import_browser_action import open_import_advanced


def statement(account, name, amount='-123.45'):
    return ('建设银行个人交易明细\n' + f'账号：{account}\n' + '姓名：Mock测试用户\n币种：人民币\n'
            '摘要,币别,交易日期,交易金额,账户余额,交易地点/附言,对方账号与户名\n'
            f'{name},CNY,2024-03-15,{amount},10000.00,{name},Mock商户\n').encode()


def run():
    with tempfile.TemporaryDirectory(prefix='paam-import-choice-') as temporary:
        app = prepare_app(Path(temporary))
        from backend.core import target_database
        from test_pirc35_import_duplicate import manifest
        from import_batch_helpers import confirm_api_batch

        def snapshot():
            with target_database.SessionLocal() as db:
                return manifest(SimpleNamespace(db=db))

        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        try:
            # The first parser load is permitted up to 30s by the real API;
            # HTTPX's default 5s read timeout is not that application budget.
            # Financial writes still retain their unchanged backend 2s guard.
            with httpx.Client(base_url=base, trust_env=False, timeout=35) as client:
                for _ in range(100):
                    try:
                        if client.get('/api/health').status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError('fictional app did not start')
                origin = client.post('/paam/import/v1/preview', json={'files':[{'filename':'Mock-origin.csv',
                    'source_type':'ccb','content_base64':base64.b64encode(statement('990000000000001234','Mock原始证据')).decode()}]})
                assert origin.status_code == 200, origin.text
                original = confirm_api_batch(client, origin.json()['body'], explicit_new=True)
                assert original.status_code == 200, original.text
                keeper_id = original.json()['body']['processed_rows'][0]['transaction_id']
                before = snapshot()
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel='msedge' if os.name == 'nt' else None, headless=True)
                    page = browser.new_page(viewport={'width':1280,'height':900})
                    errors, writes, puts, responses = [], [], [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: writes.append(request.post_data_json)
                        if request.method == 'POST' and request.url.endswith('/confirm') else None)
                    page.on('request', lambda request: puts.append(request.post_data_json)
                        if request.method == 'PUT' and '/paam/import/v1/preview/' in request.url else None)
                    page.on('response', lambda response: responses.append(response.json()['body'])
                        if response.request.method == 'POST' and response.url.endswith('/confirm') and response.status == 200 else None)
                    page.goto(base + '/#workbench/import')
                    page.locator('[data-action="import-step"][data-step="2"]').last.click()
                    upload = page.locator('[data-form="import-preview"]')
                    upload.locator('[name="files"]').set_input_files([
                        {'name':'Mock-same-source.csv','mimeType':'text/csv','buffer':statement('990000000000001234','Mock补证据')},
                        {'name':'Mock-cross-source.csv','mimeType':'text/csv','buffer':statement('990000000000009876','Mock跨源重复')},
                        {'name':'Mock-new-cash.csv','mimeType':'text/csv','buffer':statement('990000000000001234','Mock另一真实交易')},
                    ])
                    upload.locator('[data-action="preview-import"]').click()
                    rows = page.locator('[data-batch-row]')
                    expect(rows).to_have_count(3, timeout=30000)

                    # Merely opening/cancelling a decision cannot select it or
                    # acknowledge risk, and does not mutate financial tables.
                    rows.filter(has_text='Mock另一真实交易').locator('[data-row-intent]').click()
                    dialog = page.locator('dialog[open]')
                    dialog.locator('[data-import-resolution]').select_option('NEW')
                    expect(dialog.locator('[data-new-risk-ack]')).not_to_be_checked()
                    dialog.locator('[data-intent-apply]').click()
                    expect(dialog.locator('[data-intent-status]')).to_contain_text('风险确认')
                    assert writes == [] and puts == []
                    assert snapshot()['transaction_fact'] == before['transaction_fact']
                    dialog.locator('[data-workbench-close]').click()
                    expect(page.locator('[data-batch-selection]')).to_contain_text('0 行')

                    for summary, resolution, source_tail in [('Mock补证据','LINK_EXISTING','1234'),('Mock跨源重复','DUPLICATE','1234')]:
                        current = rows.filter(has_text=summary)
                        current.locator('[data-row-intent]').click()
                        dialog = page.locator('dialog[open]')
                        dialog.locator('[data-import-resolution]').select_option(resolution)
                        candidates = dialog.locator('[data-match-items] .picker-list-row')
                        expect(candidates).to_have_count(1)
                        expect(candidates.first).to_contain_text('Mock原始证据')
                        expect(candidates.first).to_contain_text(source_tail)
                        # One candidate is a suggestion, not implicit consent.
                        expect(dialog.locator('[data-intent-apply]')).to_be_disabled()
                        candidates.first.locator('[data-match-id]').click()
                        expect(dialog.locator('[data-intent-target]')).to_contain_text('Mock原始证据')
                        for width in (1440,1280,820,390):
                            page.set_viewport_size({'width':width,'height':900})
                            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                            box = dialog.bounding_box()
                            assert box['x'] >= 0 and box['x'] + box['width'] <= width + 1
                            close = dialog.locator('[data-workbench-close]')
                            assert close.evaluate('(el) => el.scrollWidth <= el.clientWidth && getComputedStyle(el).whiteSpace === "nowrap"')
                        viewport_evidence(page, f'fix-import-choice-{resolution.lower()}-390')
                        page.set_viewport_size({'width':1280,'height':900})
                        dialog.locator('[data-intent-apply]').click()
                        expect(current).to_contain_text('Mock原始证据')
                        expect(page.locator('[data-batch-save]')).to_be_enabled()
                        if resolution == 'LINK_EXISTING':
                            expect(current.locator('[data-row-account]')).to_have_count(0)
                            expect(current.locator('[data-row-ref]')).to_contain_text('保留目标Fact')

                    rows.filter(has_text='Mock另一真实交易').locator('[data-row-intent]').click()
                    dialog = page.locator('dialog[open]')
                    dialog.locator('[data-import-resolution]').select_option('NEW')
                    dialog.locator('[data-new-risk-ack]').check()
                    dialog.locator('[data-intent-apply]').click()
                    expect(page.locator('[data-batch-selection]')).to_contain_text('3 行')

                    # Change B's explicit target to the already selected NEW
                    # anchor. Transport carries stable ROW coordinates, never
                    # a guessed next Fact ID; the final result returns real IDs.
                    rows.filter(has_text='Mock跨源重复').locator('[data-row-intent]').click()
                    dialog = page.locator('dialog[open]')
                    dialog.locator('[data-import-target-kind]').select_option('ROW')
                    local = dialog.locator('[data-local-items] .picker-list-row')
                    expect(local).to_have_count(1)
                    expect(local).to_contain_text('Mock另一真实交易')
                    expect(dialog.locator('[data-intent-apply]')).to_be_disabled()
                    local.locator('[data-local-id]').click()
                    expect(dialog.locator('[data-intent-target]')).to_contain_text('Mock-new-cash.csv')
                    viewport_evidence(page, 'fix-import-choice-row-anchor')
                    dialog.locator('[data-intent-apply]').click()
                    page.locator('[data-batch-save]').click()
                    expect(page.locator('[data-batch-plan]')).to_be_enabled()
                    expect(page.locator('[data-batch-confirm]')).to_be_enabled()
                    expect(page.locator('[data-import-operation]')).to_contain_text('可规划 1 批')
                    assert writes == [] and len(puts) == 1
                    choices = puts[0]['choices']
                    assert {choice['resolution'] for choice in choices} == {'LINK_EXISTING','DUPLICATE','NEW'}
                    link = next(choice for choice in choices if choice['resolution'] == 'LINK_EXISTING')
                    assert 'account_ref_id' not in link and link['target'] == {'kind':'FACT','transaction_id':keeper_id}
                    dup = next(choice for choice in choices if choice['resolution'] == 'DUPLICATE')
                    anchor = next(choice for choice in choices if choice['resolution'] == 'NEW')
                    assert dup['target'] == {'kind':'ROW','file_id':anchor['file_id'],'source_row_number':anchor['source_row_number']}
                    pending_state = snapshot()
                    for name, records in before.items():
                        if name not in ('transaction_import_file',):
                            assert pending_state[name] == records, name
                    open_import_advanced(page)
                    page.locator('[data-batch-plan]').click()
                    expect(page.locator('[data-import-operation]')).to_contain_text('可规划 1 批', timeout=15000)
                    page.locator('[data-plan-batch]').click()
                    detail = page.locator('[data-plan-batch-detail]')
                    expect(detail).to_contain_text('真实新增 1 · 新重复 Fact 1 · 仅补证据 1')
                    viewport_evidence(page, 'fix-import-choice-complete-preview')
                    expect(page.locator('[data-batch-confirm]')).to_be_enabled()
                    def lost_reply(route):
                        response=route.fetch()
                        assert response.status==200,(response.status,response.text())
                        responses.append(response.json()['body'])
                        route.fulfill(status=503,content_type='application/json',body=json.dumps(dict(status=503,
                            message='Mock dropped ROW-anchor response',body=dict(code='RESULT_UNKNOWN'))))
                    confirm_pattern='**/paam/import/v1/preview/*/confirm'
                    page.route(confirm_pattern,lost_reply)
                    page.locator('[data-batch-confirm]').click()
                    expect(page.locator('[data-batch-status]')).to_contain_text('结果未知',timeout=15000)
                    page.unroute(confirm_pattern,lost_reply)
                    expect(page.locator('[data-batch-confirm]')).to_be_disabled()
                    assert len(writes) == 1 and len(writes[0]['selected_rows']) == 3
                    assert len(writes[0]['batch_preview_digest']) == 64
                    assert len(responses) == 1
                    processed = {(row['file_id'],row['source_row_number']):row for row in responses[0]['processed_rows']}
                    new_real = processed[(anchor['file_id'],anchor['source_row_number'])]
                    new_duplicate = processed[(dup['file_id'],dup['source_row_number'])]
                    evidence = processed[(link['file_id'],link['source_row_number'])]
                    assert new_duplicate['duplicate_kept_transaction_id'] == new_real['transaction_id']
                    assert new_duplicate['transaction_id'] != new_real['transaction_id']
                    assert evidence['transaction_id'] == keeper_id and evidence['created_review_id'] == evidence['created_ledger_id'] == 0
                    committed=snapshot()
                    with page.expect_response(lambda response:response.url.endswith('/import_file/reconcile')) as verified:
                        page.locator('[data-batch-verify]').click()
                    observed=verified.value.json()['body']
                    assert observed['fully_observed'] and len(observed['items'])==3
                    duplicate_observed=next(item for item in observed['items'] if item['resolution']=='DUPLICATE')
                    assert duplicate_observed['state']=='DUPLICATE_EXCLUDED'
                    assert duplicate_observed['transaction_id']==new_duplicate['transaction_id']
                    assert duplicate_observed['target_transaction_id']==new_real['transaction_id']
                    expect(page.locator('[data-batch-observed]')).to_be_enabled(timeout=15000)
                    expect(page.locator('[data-reconcile-rows]')).to_contain_text('不是数据库首次配对回执')
                    assert snapshot()==committed and len(writes)==1
                    viewport_evidence(page,'fix-import-reconcile-row-anchor')
                    page.locator('[data-batch-observed]').click()
                    expect(page.locator('[data-batch-selection]')).to_contain_text('本次明确选择 0 行',timeout=15000)
                    after = snapshot()
                    assert len(after['transaction_fact']) - len(before['transaction_fact']) == 2
                    assert len(after['review_case']) - len(before['review_case']) == 3
                    assert len(after['ledger_entry']) - len(before['ledger_entry']) == 3
                    assert len(after['transaction_import_row']) - len(before['transaction_import_row']) == 3
                    for name in ('transaction_fact','review_case','ledger_entry','ledger_entry_tag'):
                        assert after[name][:len(before[name])] == before[name], name
                    new_entries = after['ledger_entry'][len(before['ledger_entry']):]
                    assert sum(entry['entry_type'] == 3 for entry in new_entries) == 1
                    new_reviews = after['review_case'][len(before['review_case']):]
                    assert sorted(review['status'] for review in new_reviews) == [0,0,1]
                    assert page.evaluate("localStorage.getItem('paam.import.pending.v1')") is None
                    assert errors == [], errors
                    browser.close()
            print('PASS visible named LINK/FACT and DUP FACT/ROW selection, explicit NEW risk, complete preview digest, one real atomic import with actual anchor IDs, original keeper unchanged, four viewports; provider calls=0')
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            target_database.engine.dispose()


if __name__ == '__main__':
    run()
