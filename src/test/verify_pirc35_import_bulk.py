"""Actual paged ranges and bulk draft decisions; isolated fictional SQLite."""
import base64
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright

from serve_m2_ui import prepare_app
from browser_artifact import viewport_evidence


def statement(account, count, prefix):
    header = ('建设银行个人交易明细\n' + f'账号：{account}\n姓名：Mock测试用户\n币种：人民币\n'
        '摘要,币别,交易日期,交易金额,账户余额,交易地点/附言,对方账号与户名\n')
    return (header + ''.join(f'{prefix}{i},CNY,2024-03-15,-{i}.00,10000.00,Mock用途,Mock商户\n'
        for i in range(1,count+1))).encode()


def run():
    with tempfile.TemporaryDirectory(prefix='paam-import-bulk-') as temporary:
        app = prepare_app(Path(temporary))
        from backend.core import target_database
        from backend.service.configured_llm_analyzer import ConfiguredLlmAnalyzer
        from test_pirc35_import_duplicate import manifest
        provider_calls = []

        async def forbid_provider(*_args, **_kwargs):
            provider_calls.append(True)
            raise AssertionError('Fictional import must not call a model provider')

        ConfiguredLlmAnalyzer.analyze = forbid_provider

        def snapshot():
            with target_database.SessionLocal() as db:
                return manifest(SimpleNamespace(db=db))

        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        server = uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
        worker = threading.Thread(target=server.run,daemon=True)
        worker.start()
        try:
            with httpx.Client(base_url=base,trust_env=False,timeout=35) as client:
                for _ in range(100):
                    try:
                        if client.get('/api/health').status_code == 200: break
                    except httpx.HTTPError: pass
                    time.sleep(.1)
                else: raise RuntimeError('fictional app did not start')
                content = statement('990000000000001234',175,'Mock批量A')
                # Persist an old SKIPPED source row using the real API, not SQL
                # repair. Reupload exposes this state without changing cash.
                seed = client.post('/paam/import/v1/preview',json={'files':[dict(filename='Mock-A.csv',source_type='ccb',
                    content_base64=base64.b64encode(content).decode())]}).json()['body']
                seed_row = client.get(f"/paam/import/v1/preview/{seed['token']}/row/list",params={'preview_digest':seed['preview_digest']}).json()['body']['items'][0]
                locator = {key:seed_row[key] for key in ('file_id','source_row_number')}
                revised = client.put(f"/paam/import/v1/preview/{seed['token']}",json=dict(expected_updated_time=seed['updated_time'],
                    choices=[locator | dict(decision='SKIP')]))
                assert revised.status_code == 200,revised.text
                seed = revised.json()['body']
                skipped = client.post(f"/paam/import/v1/preview/{seed['token']}/confirm",json=dict(expected_updated_time=seed['updated_time'],
                    preview_digest=seed['preview_digest'],selected_rows=[locator]))
                assert skipped.status_code == 200 and skipped.json()['body']['skipped_count'] == 1,skipped.text
                before = snapshot()
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel='msedge' if os.name == 'nt' else None,headless=True)
                    page = browser.new_page(viewport={'width':1280,'height':900})
                    errors,puts,writes,reads = [],[],[],[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.on('request',lambda request: puts.append(request.post_data_json)
                        if request.method == 'PUT' and '/paam/import/v1/preview/' in request.url else None)
                    page.on('request',lambda request:writes.append(request.url)
                        if request.method == 'POST' and request.url.endswith('/confirm') else None)
                    page.on('request',lambda request:reads.append(request.url)
                        if request.method == 'GET' and '/preview/' in request.url and '/row/list?' in request.url else None)
                    page.goto(base+'/#workbench/import')
                    page.locator('[data-action="import-step"][data-step="2"]').last.click()
                    upload = page.locator('[data-form="import-preview"]')
                    upload.locator('[name="files"]').set_input_files([
                        {'name':'Mock-A.csv','mimeType':'text/csv','buffer':content},
                        {'name':'Mock-B.csv','mimeType':'text/csv','buffer':statement('990000000000009876',2,'Mock批量B')},
                    ])
                    upload.locator('[data-action="preview-import"]').click()
                    expect(page.locator('[data-batch-row]')).to_have_count(20,timeout=30000)
                    after_preview = snapshot()
                    for name in before:
                        if name != 'transaction_import_file':
                            assert after_preview[name] == before[name],name
                    before = after_preview # Upload may create file metadata, not financial data.
                    for size in (50,100,20):
                        page.locator('[data-batch-page-size]').select_option(str(size))
                        expect(page.locator('[data-batch-row]')).to_have_count(size,timeout=15000)
                        expect(page.locator('[data-batch-page]')).to_contain_text(f'每页 {size} 行')
                    assert {parse_qs(urlsplit(url).query)['page_size'][0] for url in reads} >= {'20','50','100'}
                    page.locator('[data-batch-range-start]').fill('6')
                    page.locator('[data-batch-range-end]').fill('107')
                    page.locator('[data-batch-select-range]').click()
                    expect(page.locator('[data-batch-status]')).to_contain_text('先选择一个文件')
                    expect(page.locator('[data-batch-selection]')).to_contain_text('0 行')
                    file_option = page.locator('[data-batch-file] option').filter(has_text='Mock-A.csv').get_attribute('value')
                    page.locator('[data-batch-file]').select_option(file_option)
                    expect(page.locator('[data-batch-page]')).to_contain_text('175 行')
                    # A failed second range page never adds the successful first
                    # hundred rows. Ranges stay bound to one original file.
                    pattern = '**/paam/import/v1/preview/*/row/list?*'
                    def fail_second(route):
                        query = parse_qs(urlsplit(route.request.url).query)
                        if query.get('page_index') == ['2'] and 'source_row_number' in query.get('filter',[''])[0]:
                            route.fulfill(status=503,content_type='application/json',body=json.dumps(dict(status=503,
                                message='Mock range second page failed',body=dict(code='QUERY_BUSY'))))
                        else: route.continue_()
                    page.route(pattern,fail_second)
                    page.locator('[data-batch-select-range]').click()
                    expect(page.locator('[data-batch-status]')).to_contain_text('原选择保留',timeout=15000)
                    expect(page.locator('[data-batch-selection]')).to_contain_text('0 行')
                    page.unroute(pattern,fail_second)
                    expect(page.locator('[data-batch-select-range]')).to_be_enabled(timeout=15000)
                    page.locator('[data-batch-select-range]').click()
                    expect(page.locator('[data-batch-selection]')).to_contain_text('101 行',timeout=15000)
                    expect(page.locator('[data-batch-bulk]')).to_be_enabled(timeout=15000)
                    expect(page.locator('[data-batch-selected-scope]')).to_contain_text('Mock-A.csv')
                    assert 'Mock-B.csv' not in page.locator('[data-batch-selected-scope]').inner_text()
                    # Reupload does not invent a cached choice from the old raw
                    # status. Explicitly keep this draft SKIP using the visible
                    # row decision; bulk recheck must later preserve it.
                    page.locator('[data-batch-row]').first.locator('[data-row-decision]').select_option('SKIP')
                    page.locator('[data-batch-bulk]').click()
                    dialog = page.locator('dialog[open]')
                    expect(dialog).to_contain_text('已选 101 行')
                    expect(dialog.locator('[data-bulk-count]')).to_contain_text('例外 1 行')
                    dialog.locator('[data-bulk-apply]').click()
                    expect(dialog.locator('[data-bulk-status]')).to_contain_text('明确排除')
                    dialog.locator('[data-workbench-close]').click()
                    assert puts == writes == [] and snapshot() == before
                    # Merely selecting NEW is not cash consent. Explicit consent
                    # plus exception exclusion changes only the named 100 drafts.
                    page.locator('[data-batch-bulk]').click()
                    dialog = page.locator('dialog[open]')
                    dialog.locator('[data-bulk-action]').select_option('NEW')
                    dialog.locator('[data-bulk-apply]').click()
                    expect(dialog.locator('[data-bulk-status]')).to_contain_text('新增真实现金')
                    dialog.locator('[data-bulk-risk-ack]').check()
                    dialog.locator('[data-bulk-apply]').click()
                    expect(dialog.locator('[data-bulk-status]')).to_contain_text('明确排除')
                    dialog.locator('[data-bulk-exclude-ack]').check()
                    for width in (1440,1280,820,390):
                        page.set_viewport_size({'width':width,'height':900})
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        assert dialog.evaluate('node => node.scrollWidth <= node.clientWidth')
                        viewport_evidence(page,f'fix-import-bulk-dialog-{width}')
                    dialog.locator('[data-bulk-apply]').click()
                    expect(dialog).to_have_count(0)
                    expect(page.locator('[data-batch-selection]')).to_contain_text('101 行')
                    expect(page.locator('[data-batch-save]')).to_be_enabled(timeout=15000)
                    assert puts == writes == [] and snapshot() == before
                    page.locator('[data-batch-save]').click()
                    # An excluded old-state row remains selected and must not
                    # slip through save; the server rejects this whole draft
                    # update until the user explicitly rechecks or deselects it.
                    expect(page.locator('[data-batch-status]')).to_contain_text('ROW_RECHECK_REQUIRED',timeout=15000)
                    expect(page.locator('[data-batch-plan]')).to_be_disabled()
                    expect(page.locator('[data-batch-bulk]')).to_be_enabled(timeout=15000)
                    assert len(puts) == 1 and len(puts[0]['choices']) == 101
                    choices = puts[0]['choices']
                    assert {row['file_id'] for row in choices} == {int(file_option)}
                    assert {row['source_row_number'] for row in choices} == set(range(6,107))
                    assert choices[0]['decision'] == 'SKIP' and choices[0].get('acknowledge_new_risk',False) is False
                    assert all(row['resolution'] == 'NEW' and row['acknowledge_new_risk'] is True for row in choices[1:])
                    assert writes == [] and snapshot() == before
                    # Recheck retains SKIP rather than silently accepting the old
                    # row. All other rows are exceptions and keep their intents.
                    page.locator('[data-batch-bulk]').click()
                    dialog = page.locator('dialog[open]')
                    dialog.locator('[data-bulk-action]').select_option('RECHECK')
                    expect(dialog.locator('[data-bulk-count]')).to_contain_text('可修改草稿 1 行 · 例外 100 行')
                    expect(dialog.locator('[data-bulk-exceptions] .import-bulk-record')).to_have_count(20)
                    dialog.locator('[data-bulk-exceptions] [data-bulk-next]').click()
                    expect(dialog.locator('[data-bulk-exceptions]')).to_contain_text('21–40 / 100')
                    dialog.locator('[data-bulk-exclude-ack]').check()
                    dialog.locator('[data-bulk-apply]').click()
                    expect(page.locator('[data-batch-save]')).to_be_enabled(timeout=15000)
                    page.locator('[data-batch-save]').click()
                    expect(page.locator('[data-batch-plan]')).to_be_enabled(timeout=15000)
                    assert len(puts) == 2
                    assert puts[1]['choices'][0]['decision'] == 'SKIP' and puts[1]['choices'][0]['recheck'] is True
                    assert all(row['resolution'] == 'NEW' and row['acknowledge_new_risk'] for row in puts[1]['choices'][1:])
                    # Sticky commands remain usable deep in the long list;
                    # the narrow toolbar must not consume most of the viewport.
                    page.locator('[data-batch-page-size]').select_option('100')
                    expect(page.locator('[data-batch-row]')).to_have_count(100,timeout=15000)
                    page.locator('.import-batch-scope > summary').click()
                    for width in (1440,1280,820,390):
                        page.set_viewport_size({'width':width,'height':900})
                        page.locator('[data-batch-row]').nth(70).scroll_into_view_if_needed()
                        toolbar = page.locator('[data-batch-toolbar]')
                        box = toolbar.bounding_box()
                        assert box and 0 <= box['y'] <= 2 and box['height'] < 300,box
                        expect(toolbar.locator('[data-batch-save]')).to_be_visible()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        viewport_evidence(page,f'fix-import-bulk-toolbar-{width}')
                    assert errors == [] and writes == [] and snapshot() == before
                    assert provider_calls == []
                    browser.close()
            print('PASS 20/50/100 pages, exact half-open named file ranges, failed page no prefix, bulk cancel/risk/exception/recheck guards, bounded exceptions, sticky four-width toolbar; zero finance and provider calls')
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            target_database.engine.dispose()
            assert not worker.is_alive()


if __name__ == '__main__':
    run()
