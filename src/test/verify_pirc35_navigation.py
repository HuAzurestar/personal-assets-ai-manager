"""Actual page-two return/reload anchors after a read-only correction visit."""
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import time

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright
from serve_m2_ui import prepare_app
from browser_artifact import viewport_evidence


def assert_compact_navigation(page):
    """Keep all four modules accessible without a second primary-nav row."""
    measurements = []
    for width in (1440, 1280, 1100, 821, 820, 760, 560, 390, 320):
        page.set_viewport_size({'width': width, 'height': 800})
        page.evaluate('window.scrollTo(0, 0)')
        nav = page.locator('.module-nav')
        expect(nav.locator('button')).to_have_count(4)
        viewport_evidence(page, f'dev17-compact-navigation-{width}')
        geometry = page.evaluate('''() => {
            const rect = node => {const r = node.getBoundingClientRect();
                return {left:r.left, right:r.right, top:r.top, height:r.height};};
            return {header:rect(document.querySelector('.module-topbar')),
                subbar:rect(document.querySelector('.module-subbar')),
                brand:rect(document.querySelector('.module-brand')),
                actions:rect(document.querySelector('.topbar-actions')),
                buttons:[...document.querySelectorAll('.module-nav button')].map(rect),
                timezone:rect(document.querySelector('[data-timezone]')),
                import:rect(document.querySelector('.topbar-actions [data-page="import"]')),
                overflow:document.documentElement.scrollWidth > innerWidth + 1};
        }''')
        assert not geometry['overflow'], (width, geometry)
        assert len({round(button['top']) for button in geometry['buttons']}) == 1, (width, geometry)
        assert geometry['header']['height'] <= (64 if width > 820 else 124), (width, geometry)
        assert geometry['subbar']['height'] <= (104 if 560 < width <= 760 else 64), (width, geometry)
        assert geometry['brand']['right'] <= geometry['actions']['left'] + 1, (width, geometry)
        assert geometry['timezone']['right'] <= geometry['import']['left'] + 1, (width, geometry)
        if width > 820:
            assert geometry['buttons'][-1]['right'] <= geometry['actions']['left'] + 1, (width, geometry)
        for button in geometry['buttons']:
            assert button['height'] >= 44 and button['left'] >= 0 and button['right'] <= width, (width, geometry)
        if width <= 560:
            assert geometry['timezone']['height'] >= 44 and geometry['import']['height'] >= 44, (width, geometry)
        for module, label in (('details', '明细'), ('overview', '概览'), ('workbench', '工作台'), ('settings', '设置')):
            button = nav.locator(f'[data-module="{module}"]')
            expect(button.locator('strong')).to_have_text(label)
            expect(button).to_have_attribute('aria-label', re.compile(label + '：.+'))
            assert button.locator('strong').evaluate('''node => {const r = node.getBoundingClientRect();
                const parent = node.parentElement.getBoundingClientRect();
                return r.left >= parent.left && r.right <= parent.right;}'''), (width, module)
        measurements.append((width, geometry['header']['height'], geometry['subbar']['height']))
    page.set_viewport_size({'width': 1280, 'height': 800})
    help_toggle = page.locator('.domain-help summary')
    help_toggle.focus()
    help_toggle.press('Enter')
    expect(page.locator('.domain-help')).to_have_attribute('open', '')
    expect(page.locator('.domain-help p')).to_be_visible()
    expect(page.locator('.domain-help p')).to_contain_text('事实保留来源，审查负责解释')
    assert page.locator('.domain-help p').evaluate('node => node.getBoundingClientRect().right <= innerWidth')
    help_toggle.press('Enter')
    expect(page.locator('.domain-help')).not_to_have_attribute('open', '')
    # Native keyboard activation, not script-driven dispatch; routes remain read-only.
    for width in (1280, 320):
        page.set_viewport_size({'width': width, 'height': 800})
        for module, destination in (('details', 'details/transaction-fact'),
                ('overview', 'overview'), ('workbench', 'workbench/import'), ('settings', 'settings/automation')):
            button = page.locator(f'.module-nav [data-module="{module}"]')
            button.focus()
            expect(button).to_be_focused()
            button.press('Enter')
            page.wait_for_function('(path) => location.hash.split("?")[0] === "#" + path', arg=destination)
            expect(button).to_have_attribute('aria-pressed', 'true')
            expect(page.locator('.module-nav [aria-pressed="true"]')).to_have_count(1)
            expect(page.locator('#page-content')).not_to_have_attribute('aria-busy', 'true')
            selected = page.locator('.secondary-nav [aria-pressed="true"]')
            expect(selected).to_have_count(1)
            assert selected.evaluate('''node => {const r = node.getBoundingClientRect();
                const outer = node.parentElement.getBoundingClientRect();
                return r.left >= outer.left - 1 && r.right <= outer.right + 1;}''')
    # Compact secondary controls still reveal the active item after a route change.
    page.goto(page.url.split('#')[0] + '#details/auto-rule')
    active = page.locator('.secondary-nav [data-page="auto-rules"]')
    expect(active).to_have_attribute('aria-pressed', 'true')
    assert active.evaluate('''node => {const r = node.getBoundingClientRect();
        const outer = node.parentElement.getBoundingClientRect();
        return r.left >= outer.left - 1 && r.right <= outer.right + 1;}''')
    page.locator('[data-timezone]').select_option('Asia/Tokyo')
    expect(page.locator('[data-timezone]')).to_have_value('Asia/Tokyo')
    expect(active).to_have_attribute('aria-pressed', 'true')
    page.reload()
    expect(active).to_have_attribute('aria-pressed', 'true')
    expect(page.locator('[data-timezone]')).to_have_value('Asia/Tokyo')
    page.locator('[data-timezone]').select_option('Asia/Hong_Kong')
    print('PASS compact shell (width, primary height, secondary height):', measurements)


def run():
    with tempfile.TemporaryDirectory(prefix='paam-pirc35-navigation-') as temporary:
        app = prepare_app(Path(temporary))
        from backend.core import target_database
        from backend.entity import TransactionFact, LedgerEntry, ReviewCase
        from backend.mapper.review_command_mapper import ReviewCommandMapper
        from sqlalchemy import select, func
        entities = (TransactionFact, LedgerEntry, ReviewCase)
        try:
            with target_database.SessionLocal() as db:
                facts = [TransactionFact(fact_key=f'mock-navigation-{index}',
                    occurred_time=datetime(2030, 1, 1, tzinfo=timezone.utc),
                    cash_direction=2, amount=1000, currency_code='CNY', account_code='',
                    summary=f'Mock navigation {index}') for index in range(48)]
                db.add_all(facts)
                db.flush()
                ReviewCommandMapper(db).create_initial_defaults([fact.id for fact in facts])
                db.commit()
                before = [db.scalar(select(func.count()).select_from(entity)) for entity in entities]
                before_tables = {table.name: tuple(tuple(row) for row in db.execute(select(table).order_by(table.c.id)))
                    for table in target_database.TargetBase.metadata.sorted_tables}
                assert len(before_tables) == 20
        except Exception:
            target_database.engine.dispose()
            raise
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        try:
            with httpx.Client(base_url=base, trust_env=False, timeout=30) as client:
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
                page = browser.new_page(viewport={'width':1280, 'height':800})
                errors, writes = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: writes.append(request.url) if request.method not in ('GET', 'HEAD') else None)
                origin = '#details/ledger?cash_direction=OUT&cash_currency_code=CNY&sort_field=id&sort_order=desc&page_size=20&page=2&account_ref_id=0'
                page.goto(base + '/' + origin)
                rows = page.locator('[data-economic-row]')
                expect(rows).to_have_count(20)
                target = rows.nth(10)
                target_id = target.get_attribute('data-economic-row')
                target.evaluate('row => window.scrollBy(0,row.getBoundingClientRect().top - 160)')
                page.wait_for_function('history.state?.paamView?.y > 100')
                original_top = target.bounding_box()['y']
                assert abs(original_top - 160) < 2
                target.locator('[data-action="economic-detail"]').click()
                drawer = page.locator('.inspection-workspace[open]')
                expect(drawer).to_be_visible()
                drawer.locator('[data-close]').click()
                assert abs(target.bounding_box()['y'] - original_top) < 2
                target.locator('[data-action="economic-detail"]').click()
                drawer.locator('[data-action="edit-ledger-account"]').click()
                expect(page.locator('[data-review-workflow]')).to_be_visible()
                page.go_back()
                expect(rows).to_have_count(20)
                expect(page.locator('#page-content')).not_to_have_attribute('aria-busy', 'true')
                assert page.url.endswith(origin)
                target = page.locator(f'[data-economic-row="{target_id}"]')
                assert abs(target.bounding_box()['y'] - original_top) < 3
                form = page.locator('[data-form="economic-filter"]')
                expect(form.locator('[name="cash_direction"]')).to_have_value('OUT')
                expect(form.locator('[name="cash_currency_code"]')).to_have_value('CNY')
                expect(form.locator('[name="sort"]')).to_have_value('id.desc')
                viewport_evidence(page, 'dev17-navigation-return')
                # Reload restores the history entry, not a server response cache.
                page.reload()
                expect(rows).to_have_count(20)
                expect(page.locator('#page-content')).not_to_have_attribute('aria-busy', 'true')
                assert abs(target.bounding_box()['y'] - original_top) < 3
                assert_compact_navigation(page)
                assert not errors and not writes, (errors, writes)
                browser.close()
            with target_database.SessionLocal() as db:
                assert before == [db.scalar(select(func.count()).select_from(entity)) for entity in entities]
                after_tables = {table.name: tuple(tuple(row) for row in db.execute(select(table).order_by(table.c.id)))
                    for table in target_database.TargetBase.metadata.sorted_tables}
                assert after_tables == before_tables, 'read-only navigation changed a business table'
            print('PASS page-two filters/record anchor/detail close/correction visit/back/reload; zero UI writes and twenty tables unchanged')
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            target_database.engine.dispose()


if __name__ == '__main__':
    run()
