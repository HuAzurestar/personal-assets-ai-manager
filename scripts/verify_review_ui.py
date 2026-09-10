"""Real Edge/Chromium checks for manual review, using a disposable ledger."""
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright


def run():
    with tempfile.TemporaryDirectory(prefix='paam-review-ui-') as temp:
        os.environ['PAAM_DATA_DIR'] = temp
        os.environ['PAAM_DATABASE_URL'] = f"sqlite:///{Path(temp) / 'ledger.db'}"
        os.environ['PAAM_LLM_PROVIDER'] = 'mock'
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        server = uvicorn.Server(uvicorn.Config('app.main:app', host='127.0.0.1', port=port, log_level='error'))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                try:
                    if httpx.get(base + '/api/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            with httpx.Client(base_url=base) as api:
                def bill(name, amount, minute):
                    return api.post('/api/bills', json={'occurred_at': f'2026-09-01T10:{minute:02}:00', 'merchant': name, 'amount': amount, 'account_name': '本人银行卡'}).json()['id']
                expense, repayment = bill('聚餐原始付款', -300, 0), bill('朋友回款', 199.99, 20)
                original, refund = bill('退款原购买', -300, 30), bill('退款到账', 100, 40)
                dirty = '交易创建时间,交易对方,金额（元）,收/支,交易号\n,漏时间,20,支出,missing-1\n'.encode()
                assert api.post('/api/imports/alipay?filename=bad.csv', content=dirty).json()['issue_count'] == 1
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='msedge' if sys.platform == 'win32' else None, headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base)
                expect(page.get_by_role('alert')).to_contain_text('待核验')
                page.get_by_role('button', name='手工事项与往来', exact=True).click()
                page.get_by_role('button', name='新建事项', exact=True).click()
                form = page.locator('[data-matter-form]')
                form.locator('[name=title]').fill('AA 往来核验')
                form.locator('[name=scenarios]').fill('AA,代付')
                def fill_line(index, bill_id, amount, role, party=''):
                    row = form.locator('[data-line]').nth(index)
                    row.locator('[name=bill]').select_option(str(bill_id))
                    row.locator('[name=amount]').fill(str(amount))
                    row.locator('[name=role]').select_option(role)
                    row.locator('[name=party]').fill(party)
                fill_line(0, expense, 100, 'expense')
                form.locator('[data-add]').click(); fill_line(1, expense, 200, 'receivable', '小王')
                form.locator('[data-add]').click(); fill_line(2, repayment, 199.99, 'repayment_received', '小王')
                form.locator('[name=reason]').fill('按三人分摊，朋友承担两份')
                # A failed save must preserve all inputs and permit a corrected retry.
                fill_line(1, expense, 201, 'receivable', '小王')
                form.get_by_role('button', name='核对并确认分配').click(); page.locator('[data-review-confirm]').click()
                expect(page.locator('.form-error')).to_contain_text('超出')
                expect(form.locator('[name=title]')).to_have_value('AA 往来核验')
                fill_line(1, expense, 200, 'receivable', '小王')
                form.get_by_role('button', name='核对并确认分配').click(); page.locator('[data-review-confirm]').click()
                expect(page.locator('dialog')).to_have_count(0)
                page.get_by_role('button', name='手工事项与往来', exact=True).click()
                expect(page.locator('dialog')).to_contain_text('0.01')
                page.get_by_role('button', name='修改关联与分配').click()
                form = page.locator('[data-matter-form]')
                fill_line(0, expense, 100.01, 'expense'); fill_line(1, expense, 199.99, 'receivable', '小王')
                form.locator('[name=reason]').fill('本人承担一分尾差')
                form.get_by_role('button', name='核对并确认分配').click(); page.locator('[data-review-confirm]').click()
                expect(page.locator('dialog')).to_have_count(0)
                print('PASS manual AA allocation, visible failure, correction and remaining balance', flush=True)

                page.goto(base + '#data?q=退款到账')
                page.locator('.desktop-table [data-action=bill-detail]').click()
                page.get_by_role('button', name='确认为退款 / 查看退款').click(); page.locator('[data-review-confirm]').click()
                page.get_by_role('button', name='关联原支出').click()
                allocation = page.get_by_role('dialog', name='关联退款与原支出', exact=True)
                allocation.locator('[name=expense]').select_option(str(original))
                allocation.locator('[name=amount]').fill('60')
                allocation.locator('[name=reason]').fill('退款单据核验')
                allocation.get_by_role('button', name='确认分配', exact=True).click()
                expect(allocation).not_to_be_attached()
                # Close the parent list and reopen to see the committed allocation.
                page.locator('dialog [data-close]').click()
                page.get_by_role('button', name='退款记录', exact=True).click()
                expect(page.locator('dialog')).to_contain_text('40.00')
                page.get_by_role('button', name='撤销此分配').click(); page.locator('[data-review-confirm]').click()
                expect(page.locator('dialog')).to_contain_text('100.00')
                page.locator('dialog [data-close]').click()
                print('PASS refund declaration, allocation and undo retain refund nature', flush=True)

                page.goto(base + '#summary')
                page.locator('[data-metric=income]').click()
                expect(page.locator('dialog')).to_contain_text('收入贡献')
                page.locator('dialog [data-close]').click()
                page.get_by_role('button', name='数据问题', exact=True).click()
                page.get_by_role('button', name='核对并修正').click()
                correction = page.locator('dialog').last
                for name, value in {'occurred_at': '2026-09-01T09:00', 'merchant': '漏时间', 'amount': '-20', 'account_name': '本人银行卡', 'reason': '核对原始平台详情'}.items():
                    correction.locator(f'[name={name}]').fill(value)
                correction.get_by_role('button', name='确认修正并入账').click()
                expect(page.locator('dialog')).to_have_count(0)
                print('PASS contribution drilldown and source-preserving data correction', flush=True)
                for width in (390, 768):
                    page.set_viewport_size({'width': width, 'height': 900})
                    page.get_by_role('button', name='手工事项与往来', exact=True).click()
                    page.get_by_role('button', name='修改关联与分配').click()
                    expect(page.locator('[data-matter-form]')).to_be_visible()
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    page.locator('dialog [data-close]').click()
                assert not errors, errors
                print('PASS narrow screens and no uncaught browser errors', flush=True)
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            from app.database import engine
            engine.dispose()


if __name__ == '__main__':
    run()
