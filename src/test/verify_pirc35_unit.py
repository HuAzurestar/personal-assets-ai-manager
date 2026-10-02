"""Real browser: dictionary recovery, every currency choice and exact settings."""
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

from browser_artifact import viewport_evidence
from serve_m2_ui import prepare_app


def run():
    with tempfile.TemporaryDirectory(prefix='paam-unit-browser-') as temporary:
        app = prepare_app(Path(temporary))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        try:
            with httpx.Client(base_url=base, trust_env=False) as client:
                for _ in range(100):
                    try:
                        if client.get('/api/health').status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError('fictional app did not start')
                dictionary = client.get('/paam/ledger/v1/unit').json()['body']['items']
            from backend.core import target_database
            from backend.entity import TransactionFact, ReviewCase, LedgerEntry
            from backend.mapper.review_command_mapper import ReviewCommandMapper
            from sqlalchemy import select, func
            with target_database.SessionLocal() as db:
                rows = [TransactionFact(fact_key=f'mock-unit-{code}',
                    occurred_time=datetime(2026, 9, 1, tzinfo=timezone.utc), cash_direction=1,
                    amount=12345, currency_code=code, account_code='', summary=f'Mock unit {code}') for code in ['KRW', 'KRW_4']]
                db.add_all(rows)
                db.flush()
                ReviewCommandMapper(db).create_initial_defaults([row.id for row in rows])
                db.commit()
                counts = [db.scalar(select(func.count()).select_from(entity))
                          for entity in [TransactionFact, ReviewCase, LedgerEntry]]
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='msedge' if os.name == 'nt' else None, headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 900})
                errors, writes, reads = [], [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda req: writes.append((req.method, req.url)) if req.method not in ['GET', 'HEAD'] else None)

                def intercept(route):
                    reads.append(route.request.url)
                    if len(reads) == 1:
                        route.fulfill(status=503, content_type='application/json', body=
                            '{"status":503,"message":"Mock unit dictionary read failure","body":{"code":"QUERY_BUSY"}}')
                    else:
                        route.continue_()
                page.route('**/paam/ledger/v1/unit', intercept)
                page.goto(base + '/#details/transaction-fact')
                expect(page.locator('#page-content')).to_contain_text('Mock unit dictionary read failure')
                page.locator('[data-action="reload"]').click()
                fact_form = page.locator('[data-form="fact-filter"]')
                currency_codes = {row['code'] for row in dictionary if row['dimension'] == 'CURRENCY'}
                select_currency = fact_form.locator('[name="currency_code"]')
                expect(select_currency).to_be_visible()
                assert set(select_currency.locator('option').evaluate_all('(nodes) => nodes.map(n => n.value)')) == currency_codes | {''}
                select_currency.select_option('KRW')
                expect(page.locator('[data-action="fact-detail"]')).to_have_count(1)
                expect(page.locator('.fact-amount')).to_contain_text('12,345 KRW')
                page.locator('[name="currency_code"]').select_option('KRW_4')
                expect(page.locator('[name="currency_code"]')).to_have_value('KRW_4')
                # Display is in the base currency; the filter retains exact unit.
                expect(page.locator('.fact-amount')).to_contain_text('1.2345 KRW')
                viewport_evidence(page, 'fix-r13-fact-unit')
                page.evaluate("location.hash = '#details/ledger'")
                flow_form = page.locator('[data-form="economic-filter"]')
                flow_currency = flow_form.locator('[name="cash_currency_code"]')
                expect(flow_currency).to_be_visible()
                assert set(flow_currency.locator('option').evaluate_all('(nodes) => nodes.map(n => n.value)')) == currency_codes | {''}
                flow_currency.select_option('KRW')
                expect(page.locator('[data-action="economic-detail"]')).to_have_count(1)
                expect(page.locator('.fact-amount')).to_contain_text('12,345 KRW')
                # Shared registry serves every route in one page lifecycle.
                page.evaluate("location.hash = '#workbench/position'")
                page.locator('[data-position-create]').click()
                dialog = page.locator('dialog[open]')
                units = dialog.locator('[name="unit_code"]')
                expect(units).to_be_visible()
                assert set(units.locator('option').evaluate_all('(nodes) => nodes.map(n => n.value)')) == {row['code'] for row in dictionary}
                units.select_option('KG_3')
                dialog.locator('[data-workbench-close]').click()
                page.evaluate("location.hash = '#settings/automation'")
                page.locator('[data-action="disclosure-edit"]').click()
                form = page.locator('[data-disclosure-form]')
                choices = form.locator('[data-new-currency]')
                assert 'PCS' not in choices.locator('option').evaluate_all('(nodes) => nodes.map(n => n.value)')
                assert 'KG_3' not in choices.locator('option').evaluate_all('(nodes) => nodes.map(n => n.value)')
                for code, value in [('KRW', '400'), ('KRW_4', '0.0001')]:
                    choices.select_option(code)
                    form.locator('[data-add-currency]').click()
                    row = form.locator('[data-currency-row]').filter(has_text=f'{code} 区间')
                    row.locator('[data-add-boundary]').click()
                    row.locator('[data-boundary]').nth(1).fill(value)
                viewport_evidence(page, 'fix-r13-disclosure-unit')
                form.locator('[name="acknowledged"]').check()
                form.locator('button[type="submit"]').click()
                expect(page.locator('dialog[open]')).to_have_count(0)
                setting = httpx.get(base + '/paam/system/v1/setting/automation', trust_env=False).json()['body']
                assert setting['disclosure']['amount_bands']['KRW']['boundaries'] == [0, 400]
                assert setting['disclosure']['amount_bands']['KRW_4']['boundaries'] == [0, 1]
                assert len(reads) == 2, reads
                assert len(writes) == 1 and writes[0][0] == 'PUT' and writes[0][1].endswith('/setting/automation'), writes
                page.reload()
                expect(page.locator('[data-auto-disclosure]')).to_contain_text('KRW')
                assert len(reads) == 3
                page.locator('[data-action="disclosure-edit"]').click()
                form = page.locator('[data-disclosure-form]')
                krw = form.locator('[data-currency-row]').filter(has_text='KRW 区间')
                expect(krw.locator('[data-boundary]').nth(1)).to_have_value('400')
                with target_database.SessionLocal() as db:
                    assert counts == [db.scalar(select(func.count()).select_from(entity))
                                      for entity in [TransactionFact, ReviewCase, LedgerEntry]]
                assert not errors, errors
                browser.close()
            print('PASS full unit choices, shared read/retry, exact KRW/suffix filters and persisted disclosure; no financial HTTP writes/providers')
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            from backend.core import target_database
            target_database.engine.dispose()


if __name__ == '__main__':
    run()
