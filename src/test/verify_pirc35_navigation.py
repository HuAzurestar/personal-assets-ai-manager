"""Actual page-two return/reload anchors after a read-only correction visit."""
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
    with tempfile.TemporaryDirectory(prefix='paam-pirc35-navigation-') as temporary:
        app = prepare_app(Path(temporary))
        from backend.core import target_database
        from backend.entity import TransactionFact, LedgerEntry, ReviewCase
        from backend.mapper.review_command_mapper import ReviewCommandMapper
        from sqlalchemy import select, func
        entities = (TransactionFact, LedgerEntry, ReviewCase)
        with target_database.SessionLocal() as db:
            facts = [TransactionFact(fact_key=f'mock-navigation-{index}',
                occurred_time=datetime(2030, 1, 1, tzinfo=timezone.utc),
                cash_direction=2, amount=1000, currency_code='CNY',
                summary=f'Mock navigation {index}') for index in range(48)]
            db.add_all(facts)
            db.flush()
            ReviewCommandMapper(db).create_initial_defaults([fact.id for fact in facts])
            db.commit()
            before = [db.scalar(select(func.count()).select_from(entity)) for entity in entities]
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
                assert not errors and not writes, (errors, writes)
                browser.close()
            with target_database.SessionLocal() as db:
                assert before == [db.scalar(select(func.count()).select_from(entity)) for entity in entities]
            print('PASS page-two filters/record anchor/detail close/correction visit/back/reload; zero UI writes')
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            target_database.engine.dispose()


if __name__ == '__main__':
    run()
