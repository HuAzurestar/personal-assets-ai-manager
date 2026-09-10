"""Run real-browser regressions against a disposable local database.

Install playwright separately, then run with the project's Python interpreter.
Never connects to an existing user service or changes its database.
"""
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import threading
import re

import httpx
import pyzipper
import uvicorn
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'ui-verification'
OUT.mkdir(parents=True, exist_ok=True)
checks = []


def passed(name):
    checks.append(name)
    print(f'PASS {name}', flush=True)


def csv_file(name, rows):
    header = '交易时间,交易类型,交易对手,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注\n'
    lines = [f'{dt},商户消费,{merchant},午餐,{direction},{amount},{account},支付成功,{name}-{i},order-{i},体验账单' for i, (dt, merchant, direction, amount, account) in enumerate(rows)]
    return {'name': name + '.csv', 'mimeType': 'text/csv', 'buffer': (header + '\n'.join(lines)).encode()}


with tempfile.TemporaryDirectory(prefix='paam-ui-') as temp:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    env = {**os.environ, 'PAAM_DATA_DIR': temp, 'PAAM_DATABASE_URL': f'sqlite:///{Path(temp) / "test.db"}', 'PAAM_LLM_PROVIDER': 'mock'}
    with (OUT / 'server.log').open('w', encoding='utf-8') as log:
        os.environ.update(env)
        sys.path.insert(0, str(ROOT))
        server = uvicorn.Server(uvicorn.Config('app.main:app', host='127.0.0.1', port=port, log_level='error'))
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        try:
            for _ in range(100):
                try:
                    if httpx.get(base + '/api/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError('Test service did not start')
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel='msedge' if os.name == 'nt' else 'chromium', headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000}, locale='zh-CN')
                page.set_default_timeout(10000)
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                def nav(name):
                    page.locator(f'[data-page="{name}"]').click()
                    expect(page).to_have_url(re.compile(r'#' + name + r'(\?|$)'))
                    expect(page.locator('#page-content')).not_to_have_attribute('aria-busy', 'true')
                def submit():
                    page.locator('[data-form="filters"] button[type="submit"], [data-form="filters"] button:not([data-action])').first.click()
                    expect(page.locator('#page-content')).not_to_have_attribute('aria-busy', 'true')
                def screenshot(name):
                    page.screenshot(path=str(OUT / f'{name}.png'))
                def open_import(file):
                    page.locator('.page-header [data-action=import]').click()
                    page.locator('#import-dialog select[name=provider]').select_option('wechat')
                    page.locator('#import-files').set_input_files(file)
                def preview():
                    page.locator('[data-action=preview-import]').click()
                    expect(page.locator('#confirm-import')).to_be_visible()
                def confirm():
                    page.locator('#confirm-import').click()
                    expect(page.locator('#import-preview .success')).to_be_visible()
                page.goto(base)
                expect(page.get_by_text('还没有收支记录')).to_be_visible()
                expect(page.locator('html')).to_have_attribute('data-theme', 'focus')
                nav('settings')
                for theme in ['jade', 'blue', 'paper', 'focus']:
                    page.locator(f'[data-theme-choice={theme}]').click()
                    expect(page.locator('html')).to_have_attribute('data-theme', theme)
                    page.reload()
                    expect(page.locator(f'[data-theme-choice={theme}]')).to_have_attribute('aria-pressed', 'true')
                nav('summary')
                passed('four themes apply immediately and persist; Focus is default')
                page.locator('#nav-toggle').click();page.reload()
                expect(page.locator('.workspace')).to_have_attribute('data-collapsed','true')
                page.locator('#nav-toggle').click()
                passed('sidebar preference persists after reload')
                screenshot('01-empty')
                a = csv_file('A', [('2026-09-08 09:00:00', 'A商户', '支出', 10, '零钱')])
                b = csv_file('B', [('2026-09-08 09:00:00', 'B商户', '支出', 10, '零钱')])
                open_import(a); preview()
                page.locator('#import-files').set_input_files(b)
                expect(page.locator('#confirm-import')).to_have_count(0)
                preview()
                expect(page.locator('#import-preview')).to_contain_text('B商户')
                with page.expect_request('**/api/imports/wechat/batch') as req:
                    confirm()
                assert req.value.post_data_json['files'][0]['filename'] == 'B.csv'
                page.locator('[data-action=import-done]').click()
                expect(page.locator('#transaction-result')).to_contain_text('B商户')
                passed('changing files invalidates preview and imports visible selection')
                rows = [(f'2026-09-08 10:{i:02}:00', f'日常商户{i}', '支出', i+1, '零钱') for i in range(45)]
                rows += [('2026-09-08 12:00:00', '重复咖啡', '支出', 25, '零钱'), ('2026-09-08 12:01:00', '重复咖啡', '支出', 25, '零钱'), ('2026-09-08 13:00:00', '本人转账', '支出', 500, '银行卡'), ('2026-09-08 13:01:00', '本人转账', '收入', 500, '余额')]
                fixture = csv_file('sample', rows)
                open_import(fixture); preview(); screenshot('02-preview'); confirm()
                expect(page.locator('#import-preview .success')).to_contain_text('49 条流水')
                page.locator('[data-action=import-done]').click()
                expect(page.locator('#transaction-result .range')).to_contain_text('共 50 条')
                passed('multi-row import displays real preview and transaction count')
                open_import(fixture); preview()
                expect(page.locator('#confirm-import')).to_be_disabled()
                expect(page.locator('#import-preview')).to_contain_text('已导入，将跳过')
                page.locator('#import-dialog [data-close]').click()
                passed('duplicate file cannot be confirmed')
                buf = io.BytesIO()
                with pyzipper.AESZipFile(buf, 'w', compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as archive:
                    archive.setpassword(b'ui-password'); archive.writestr('bill.csv', csv_file('zip', [('2026-09-07 10:00:00', 'ZIP商户', '支出', 15, '零钱')])['buffer'])
                open_import({'name':'test.zip','mimeType':'application/zip','buffer':buf.getvalue()})
                page.locator('#import-password').fill('ui-password'); preview()
                expect(page.locator('#import-password')).to_have_value('')
                page.locator('#confirm-import').click()
                expect(page.locator('#import-dialog .form-error')).to_contain_text('再次输入')
                page.locator('#import-password').fill('ui-password'); confirm()
                page.locator('[data-action=import-done]').click()
                passed('ZIP password is cleared, required again, and import succeeds')
                expect(page.locator('#transaction-result .range')).to_contain_text('51 条')
                page.locator('input[name=q]').fill('B商户'); submit()
                expect(page.locator('#transaction-result .range')).to_contain_text('共 1 条')
                page.locator('.desktop-table [data-action=assign]').click()
                expect(page.locator('dialog')).to_be_visible()
                page.keyboard.press('Escape')
                expect(page.locator('dialog')).to_have_count(0)
                expect(page.locator('.desktop-table [data-action=assign]')).to_be_focused()
                page.locator('.desktop-table [data-action=assign]').click()
                page.locator('dialog select[name=category]').select_option('transport')
                page.route('**/api/transactions/*/tag-state', lambda r:r.fulfill(status=500, content_type='application/json', body='{"detail":"模拟失败"}'))
                page.get_by_role('button', name='保存标签', exact=True).click()
                expect(page.locator('dialog .form-error')).to_have_text('模拟失败')
                expect(page.locator('dialog select[name=category]')).to_have_value('transport')
                page.unroute('**/api/transactions/*/tag-state')
                page.get_by_role('button', name='保存标签', exact=True).click()
                expect(page.locator('dialog')).to_have_count(0)
                expect(page.locator('#transaction-result')).to_contain_text('交通')
                passed('failed tag save preserves input and retries successfully')
                nav('summary'); page.go_back()
                expect(page.locator('input[name=q]')).to_have_value('B商户')
                page.locator('.desktop-table [data-action=bill-detail]').click()
                expect(page.locator('dialog')).to_contain_text('B.csv')
                page.locator('dialog [data-close]').click()
                passed('browser back restores filters and source detail works')
                nav('tags');page.locator('[data-action=new-view]').click();page.locator('dialog input[name=name]').fill('测试项目');page.locator('dialog button.primary').click()
                expect(page.locator('dialog')).to_have_count(0)
                project=page.locator('.tag-grid .panel').filter(has_text='测试项目')
                project.locator('[data-action=new-tag]').click();page.locator('dialog input[name=name]').fill('旅行');page.locator('dialog button.primary').click()
                expect(project).to_contain_text('旅行')
                project.locator('[data-action=edit-tag]').click();page.locator('dialog input[name=name]').fill('出游');page.locator('dialog button.primary').click()
                expect(project).to_contain_text('出游')
                project.locator('[data-action=archive-tag]').click();page.locator('[data-action=confirm-definition]').click()
                expect(project).not_to_contain_text('出游')
                page.locator('#show-archived').check();expect(project).to_contain_text('出游')
                project.locator('[data-action=archive-tag]').click();page.locator('[data-action=confirm-definition]').click();expect(page.locator('dialog')).to_have_count(0)
                screenshot('03-tags');passed('create rename archive restore tags')
                nav('data');page.locator('[data-action=clear-filters]').first.click();expect(page.locator('#transaction-result .range')).to_contain_text('51 条')
                page.locator('#select-all-bills').check();page.locator('[data-action=bulk-tags]').click()
                page.locator('dialog select[name=scenario]').select_option('daily');page.get_by_role('button',name='保存标签',exact=True).click();expect(page.locator('dialog')).to_have_count(0)
                passed('bulk labels can update one dimension')
                page.set_viewport_size({'width':1920,'height':600})
                page.locator('.desktop-table [data-action=assign]').last.click()
                old_y=page.evaluate('scrollY')
                old_table_y=page.locator('.desktop-table .table-wrap').evaluate('(el)=>el.scrollTop')
                page.get_by_role('button',name='保存标签',exact=True).click()
                expect(page.locator('dialog')).to_have_count(0)
                expect(page.locator('#page-content')).not_to_have_attribute('aria-busy','true')
                assert abs(page.evaluate('scrollY')-old_y)<3
                assert abs(page.locator('.desktop-table .table-wrap').evaluate('(el)=>el.scrollTop')-old_table_y)<3
                page.locator('.desktop-table [data-select-bill]').last.check()
                expect(page.locator('#bill-selection')).to_be_in_viewport()
                page.locator('[data-action=cancel-selection]').click()
                expect(page.locator('#bill-selection')).to_be_hidden()
                passed('saving preserves page and table position; bulk actions stay reachable')
                page.set_viewport_size({'width':1440,'height':1000})
                page.locator('[data-step="1"]').click();expect(page.locator('.pagination')).to_contain_text('第 2 /')
                assert page.evaluate('scrollY') < 50
                passed('pagination scrolls to start')
                nav('candidates')
                duplicate=page.locator('.candidate-card').filter(has_text='重复咖啡')
                duplicate.locator('[data-action=candidate-detail]').click()
                expect(page.locator('dialog details[open]')).to_have_count(2)
                page.locator('dialog [data-decision=resolve_duplicate]').first.click()
                expect(page.locator('[data-review-confirm]')).to_be_visible()
                page.locator('[data-review-cancel]').click()
                expect(page.locator('#candidate-detail-dialog')).to_be_visible()
                expect(duplicate).not_to_contain_text('已保留一笔')
                page.locator('dialog [data-decision=resolve_duplicate]').first.click()
                page.locator('[data-review-confirm]').click()
                expect(page.locator('dialog')).to_have_count(0)
                expect(duplicate).to_contain_text('已保留一笔')
                transfer=page.locator('.candidate-card').filter(has_text='本人转账')
                transfer.locator('[data-decision=confirm_personal_transfer]').click();page.locator('[data-review-confirm]').click();expect(transfer).to_contain_text('个人转移已确认')
                page.locator('.toast button').click();expect(transfer).to_contain_text('待复核')
                transfer.locator('[data-decision=confirm_personal_transfer]').click();page.locator('[data-review-confirm]').click();expect(transfer).to_contain_text('个人转移已确认')
                page.locator('select[name=status]').select_option('transfer_grouped');submit();expect(page.locator('.candidate-card')).to_have_count(1)
                page.locator('[data-action=candidate-undo]').click();expect(page.locator('.candidate-card')).to_have_count(0)
                passed('candidate decisions undo and confirmed-transfer filter')
                nav('data');page.locator('select[name=scope]').select_option('excluded');submit();expect(page.locator('#transaction-result .range')).to_contain_text('共 1 条')
                page.locator('select[name=scope]').select_option('all');submit();expect(page.locator('#transaction-result .range')).to_contain_text('共 51 条')
                passed('excluded originals remain accessible')
                nav('tags');nav('summary')
                page.route('**/api/tag-views*',lambda r:r.fulfill(status=500,content_type='application/json',body='{"detail":"模拟加载失败"}'))
                nav('tags');expect(page.locator('#page-content .error')).to_have_text('模拟加载失败')
                page.unroute('**/api/tag-views*');page.locator('[data-action=retry]').click();expect(page.locator('.tag-grid')).to_be_visible()
                passed('page load failure is visible and retry restores content')
                calls=[]
                page.on('request',lambda r:calls.append(r.url) if '/api/database/tables/bills?' in r.url else None)
                for _ in range(3):nav('database');nav('summary')
                nav('database');calls.clear();page.locator('[data-step="1"]').click();expect(page.locator('.pagination')).to_contain_text('第 2 /')
                assert len(calls)==1,calls
                passed('one database page click issues one request after revisits')
                nav('candidates');page.locator('[data-action=clear-filters]').click();expect(page.locator('.candidate-card')).to_have_count(2)
                for width in [390,740,768,1024,1440]:
                    page.set_viewport_size({'width':width,'height':900})
                    for name in ['summary','data','tags','candidates','database']:
                        nav(name)
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'), (width,name)
                        if width <= 768:
                            assert page.locator('.sidebar').bounding_box()['height'] < 90
                        if width in [390,768,1440]:screenshot(f'{width}-{name}')
                    if width==390:
                        nav('data')
                        assert page.locator('.transaction-card').first.bounding_box()['y']<800
                passed('all five pages fit 390 740 768 1024 1440px; mobile ledger visible')
                for width,height in [(2880,1080),(3840,1440),(5120,1440),(1920,600),(900,2400)]:
                    page.set_viewport_size({'width':width,'height':height})
                    nav('data')
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    if width>1100:
                        side=page.locator('.sidebar').bounding_box()
                        main=page.locator('.workspace-main').bounding_box()
                        assert main['x']-side['x']-side['width']<50
                    if width==900:
                        assert page.locator('.sidebar').bounding_box()['height']<90
                    screenshot(f'layout-{width}-{height}')
                passed('ultrawide navigation stays adjacent; portrait navigation is compact')
                page.goto(base+'/static/style-cards.html');expect(page.locator('.card')).to_have_count(4);screenshot('style-cards')
                assert not errors,errors
                passed('four style cards and no uncaught browser errors')
                browser.close()
        finally:
            server.should_exit = True
            server_thread.join(timeout=15)
            from app.database import engine
            engine.dispose()
            (OUT/'checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
print(f'{len(checks)} browser regression groups passed. Evidence: {OUT}')
