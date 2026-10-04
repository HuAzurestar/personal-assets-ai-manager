"""Actual UI/API/SQLite import chain on six fully fictional 811-row CSVs."""
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


def statements():
    sizes = [200, 200, 150, 100, 100, 61]
    header = ('建设银行个人交易明细\n账号：990000000000007654\n姓名：Mock测试用户\n币种：人民币\n'
              '摘要,币别,交易日期,交易金额,账户余额,交易地点/附言,对方账号与户名\n')
    files, serial, valid = [], 0, []
    for file, size in enumerate(sizes):
        entries = []
        for row in range(size):
            serial += 1
            amount = f'-{serial}.25'
            if file == 1 and row < 10:
                amount = f'-{row + 1}.25'
            if file == 4 and row in (8, 44):
                amount = f'-{550 + (13 if row == 8 else 71)}.25'
            if file == 5 and row >= 52:
                amount = 'not-money'
            else:
                valid.append(amount)
            entries.append(f'Mock-{file + 1}-{row + 1},CNY,2034-01-01,{amount},10000.00,Mock附言,Mock商户\n')
        files.append({'name': f'Mock-workflow-{file + 1}.csv', 'mimeType': 'text/csv',
                      'buffer': (header + ''.join(entries)).encode()})
    assert sum(sizes) == 811 and len(valid) == 802
    return files


def run():
    with tempfile.TemporaryDirectory(prefix='paam-import-workflow-') as temporary:
        app = prepare_app(Path(temporary))
        from backend.core import target_database
        from test_pirc35_import_duplicate import manifest
        from backend.entity import TransactionFact, LedgerEntry, TransactionImportRow
        from sqlalchemy import func, select

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
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel='msedge' if os.name == 'nt' else None, headless=True)
                    page = browser.new_page(viewport={'width':1280,'height':900})
                    errors, writes, previews, confirmations = [], [], [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: writes.append(request.post_data_json)
                            if request.method == 'POST' and request.url.endswith('/confirm') else None)
                    page.on('response', lambda response: previews.append(response.json()['body'])
                            if response.request.method == 'POST' and response.url.endswith('/preview') and response.status == 200 else None)
                    page.on('response', lambda response: confirmations.append(response.json()['body'])
                            if response.request.method == 'POST' and response.url.endswith('/confirm') and response.status == 200 else None)
                    page.goto(base + '/#workbench/import')
                    page.locator('[data-action="import-step"][data-step="2"]').last.click()
                    upload = page.locator('[data-form="import-preview"]')
                    upload.locator('[name="files"]').set_input_files(statements())
                    upload.locator('[data-action="preview-import"]').click()
                    expect(page.locator('[data-batch-row]')).to_have_count(20, timeout=30000)
                    expect(page.locator('[data-batch-summary]')).to_contain_text('拟新增 802')
                    expect(page.locator('[data-batch-summary]')).to_contain_text('行问题 9')
                    assert len(previews) == 1
                    token = previews[0]['token']
                    original = snapshot()
                    with target_database.SessionLocal() as db:
                        before_facts = db.scalar(select(func.count(TransactionFact.id)))
                        before_cash = db.scalar(select(func.count(LedgerEntry.id)))
                    page.locator('[data-batch-select-scope]').click()
                    expect(page.locator('[data-batch-selection]')).to_contain_text('811 行', timeout=30000)
                    expect(page.locator('[data-batch-selected-scope]')).to_contain_text('接受 778，跳过 33')
                    expect(page.locator('[data-batch-risk-summary]')).to_contain_text('疑似重复 24')
                    expect(page.locator('[data-batch-confirm]')).to_be_disabled()
                    page.locator('[data-batch-save]').click()
                    expect(page.locator('[data-batch-confirm]')).to_be_enabled(timeout=30000)
                    assert writes == [] and snapshot() == original
                    # Reproduce 802 ACCEPT / 9 SKIP with deliberate UI edits;
                    # defaults never silently grant cash consent to those risks.
                    page.locator('[data-batch-bulk]').click()
                    dialog = page.locator('dialog[open]')
                    dialog.locator('[data-bulk-action]').select_option('ACCEPT')
                    dialog.locator('[data-bulk-apply]').click()
                    expect(page.locator('[data-batch-bulk]')).to_be_enabled(timeout=15000)
                    page.locator('[data-batch-classification]').select_option('INVALID')
                    expect(page.locator('[data-batch-row]')).to_have_count(9, timeout=15000)
                    for row in page.locator('[data-batch-row]').all():
                        row.locator('[data-row-decision]').select_option('SKIP')
                    expect(page.locator('[data-batch-selected-scope]')).to_contain_text('接受 802，跳过 9')
                    page.locator('[data-batch-save]').click()
                    expect(page.locator('[data-plan-issue-summary]')).to_contain_text('未解决 24 行', timeout=30000)
                    expect(page.locator('[data-batch-selection]')).to_contain_text('选择已保存')
                    expect(page.locator('[data-batch-confirm]')).to_be_disabled()
                    page.locator('[data-batch-confirm]').evaluate('node=>node.onclick()')
                    assert writes == [] and snapshot() == original
                    issues = page.locator('[data-plan-all-issues]')
                    expect(issues).to_contain_text('1–20 / 24')
                    issues.locator('[data-plan-next]').click()
                    expect(issues).to_contain_text('21–24 / 24')
                    expect(issues).to_contain_text('文件 #')
                    expect(issues).to_contain_text('新增现金风险尚未明确')
                    viewport_evidence(page, 'fix-import-workflow-811-blocked')
                    # The API also rejects those same saved choices, with no
                    # accounting mutation. The UI already prevented this POST.
                    current = client.get(f'/paam/import/v1/preview/{token}').json()['body']
                    selected = []
                    for index in range(1, 10):
                        response = client.get(f'/paam/import/v1/preview/{token}/row/list', params={
                            'preview_digest':current['preview_digest'],'page_index':index,'page_size':100})
                        assert response.status_code == 200, response.text
                        selected.extend({key:row[key] for key in ('file_id','source_row_number')}
                                        for row in response.json()['body']['items'])
                    assert len(selected) == 811
                    rejected = client.post(f'/paam/import/v1/preview/{token}/confirm', json={
                        'expected_updated_time':current['updated_time'],'preview_digest':current['preview_digest'],
                        'selected_rows':selected})
                    assert rejected.status_code == 422 and rejected.json()['body']['code'] == 'IMPORT_REVIEW_REQUIRED'
                    assert snapshot() == original
                    # Risk overview retains all 24 rows, including the last page.
                    page.locator('[data-batch-risk-list]').click()
                    dialog = page.locator('dialog[open]')
                    expect(dialog.locator('[data-risk-rows]')).to_contain_text('1–20 / 24')
                    dialog.locator('[data-bulk-next]').click()
                    expect(dialog.locator('[data-risk-rows]')).to_contain_text('21–24 / 24')
                    dialog.locator('[data-workbench-close]').click()
                    page.locator('[data-batch-risk-skip]').click()
                    dialog = page.locator('dialog[open]')
                    dialog.locator('[data-risk-skip-apply]').click()
                    expect(dialog.locator('[data-risk-status]')).to_contain_text('请先明确确认')
                    assert writes == [] and snapshot() == original
                    dialog.locator('[data-risk-skip-ack]').check()
                    dialog.locator('[data-risk-skip-apply]').click()
                    expect(page.locator('[data-batch-save]')).to_be_enabled(timeout=15000)
                    expect(page.locator('[data-batch-selected-scope]')).to_contain_text('接受 778，跳过 33')
                    page.locator('[data-batch-save]').click()
                    expect(page.locator('[data-batch-confirm]')).to_be_enabled(timeout=30000)
                    # All public UI widths stay usable with the new risk panel.
                    for width in (1280, 820, 390):
                        page.set_viewport_size({'width':width,'height':900})
                        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                        assert page.locator('[data-batch-toolbar]').bounding_box()['height'] <= 300
                        viewport_evidence(page, f'fix-import-workflow-ready-{width}')
                    page.set_viewport_size({'width':1280,'height':900})
                    page.locator('[data-batch-confirm]').click()
                    expect(page.locator('[data-batch-files]')).to_contain_text('剩余 0', timeout=30000)
                    assert len(writes) == len(confirmations) == 1
                    assert writes[0]['batch_preview_digest'] and len(writes[0]['selected_rows']) == 811
                    result = confirmations[0]
                    assert result['new_fact_count'] == 778 and result['skipped_count'] + result['invalid_count'] == 33
                    assert result['remaining_count'] == 0
                    with target_database.SessionLocal() as db:
                        assert db.scalar(select(func.count(TransactionFact.id))) == before_facts + 778
                        assert db.scalar(select(func.count(LedgerEntry.id))) == before_cash + 778
                        evidence = db.execute(select(TransactionImportRow.row_status,TransactionImportRow.transaction_fact_id,
                            TransactionImportRow.raw_payload).where(TransactionImportRow.transaction_import_file_id.in_(
                                [file['file_id'] for file in current['files']]))).all()
                        assert len(evidence) == 811 and all(row.raw_payload for row in evidence)
                        assert sum(row.row_status == 1 for row in evidence) == 778
                        assert sum(row.row_status != 1 and row.transaction_fact_id == 0 for row in evidence) == 33
                    assert errors == [], errors
                    browser.close()
                print('PASS actual six-file 811/9/24 UI defaults, complete risk disclosure, API parity rejection with20-table rollback, acknowledged skip, 778 atomic outputs, zero replay')
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            target_database.engine.dispose()


if __name__ == '__main__':
    run()
