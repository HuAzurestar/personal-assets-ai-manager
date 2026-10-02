"""Actual stable draft references, named selection and timezone intent; no publication."""
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
    with tempfile.TemporaryDirectory(prefix='paam-pirc35-draft-') as temporary:
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
                response = client.post('/paam/ledger/v1/account-party', json={'name': 'Mock draft person'})
                response.raise_for_status()
            from backend.core import target_database
            from backend.entity import TransactionFact, ReviewCase, LedgerEntry
            from backend.mapper.review_command_mapper import ReviewCommandMapper
            from sqlalchemy import select, func
            with target_database.SessionLocal() as db:
                db.add(TransactionFact(id=3, fact_key='mock-stable-draft', occurred_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    cash_direction=2, amount=40000, currency_code='CNY', account_code='', summary='Mock principal cash'))
                db.flush()
                ReviewCommandMapper(db).create_initial_defaults([3])
                db.commit()
                before = [db.scalar(select(func.count()).select_from(model)) for model in (TransactionFact, ReviewCase, LedgerEntry)]
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='msedge' if os.name == 'nt' else None, headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 900})
                errors, previews, commands = [], [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: previews.append(request.post_data_json)
                    if request.method == 'POST' and request.url.endswith('/review/preview')
                    else commands.append(request.url) if request.method == 'POST' and request.url.endswith('/review/command') else None)
                page.goto(base + '/#workbench/review?facts=3&case_code=BORROW_REPAY')
                form = page.locator('[data-immutable-review]')
                expect(form.locator('[data-cash-row]')).to_have_count(1)
                cash_a = form.locator('[data-cash-row]').first
                cash_a.locator('[name="cash_amount"]').fill('200')
                form.locator('[data-add-cash]').click()
                cash_b = form.locator('[data-cash-row]').last
                cash_b.locator('[name="cash_amount"]').fill('200')
                cash_a_id, cash_b_id = cash_a.get_attribute('data-draft-id'), cash_b.get_attribute('data-draft-id')
                form.locator('[data-new-position]').click()
                unused = form.locator('[data-position-draft]').first
                unused.locator('[name="title"]').fill('Mock unused object')
                form.locator('[data-new-position]').click()
                principal = form.locator('[data-position-draft]').last
                principal.locator('[name="title"]').fill('Mock principal object')
                principal.locator('[name="usage_scenario"]').select_option('PERSONAL-LENDING')
                principal.locator('[data-pick-party]').click()
                page.locator('dialog[open] [data-party-picker] [data-picker-id]').click()
                expect(principal.locator('[data-choice-label]')).to_contain_text('Mock draft person')
                target = 'new:' + principal.get_attribute('data-draft-id')

                def leg(basis):
                    form.locator('[data-add-leg]').click()
                    node = form.locator('[data-leg-row]').last
                    node.locator('[name="target"]').select_option(target)
                    node.locator('[name="leg_amount"]').fill('200')
                    node.locator('[name="occurred_time_timezone"]').select_option('Asia/Hong_Kong')
                    node.locator('[name="occurred_time"]').fill('2024-01-01T08:00:03')
                    node.locator('[name="basis"]').fill(basis)
                    return node.get_attribute('data-draft-id')

                leg_a_id, leg_b_id = leg('Mock quantity A'), leg('Mock quantity B')
                for cash_id, leg_id in [(cash_a_id, leg_a_id), (cash_b_id, leg_b_id)]:
                    form.locator('[data-add-link]').click()
                    link = form.locator('[data-link-row]').last
                    link.locator('[name="allocation_ref"]').select_option(cash_id)
                    link.locator('[name="leg_ref"]').select_option(leg_id)
                    link.locator('[name="cash_amount"]').fill('200')
                # Removing an earlier unrelated object must not retarget B to
                # the vanished old array index. Reordering retains all links.
                unused.locator('[data-remove]').click()
                cash_b.locator('[data-move-up]').click()
                form.locator(f'[data-leg-row][data-draft-id="{leg_b_id}"] [data-move-up]').click()
                form.locator('[data-add-cash]').click()
                form.locator('[data-cash-row]').last.locator('[data-remove]').click()
                expect(form.locator('[data-link-row]')).to_have_count(2)
                expect(form.locator('[data-link-row]').first.locator('[name="allocation_ref"]')).to_have_value(cash_a_id)
                expect(form.locator('[data-link-row]').first.locator('[name="leg_ref"]')).to_have_value(leg_a_id)
                expect(form.locator('[data-leg-row]').first.locator('[name="target"]')).to_have_value(target)
                # Deleting a referenced leg blocks preview locally, while the
                # other link survives and can still be inspected/edited.
                form.locator(f'[data-leg-row][data-draft-id="{leg_a_id}"] [data-remove]').click()
                expect(form.locator('[data-link-row]').last.locator('[name="leg_ref"]')).to_have_value(leg_b_id)
                form.locator('[data-review-preview]').click()
                expect(form.locator('[data-review-status]')).to_contain_text('请选择数量腿')
                assert not previews
                leg_c_id = leg('Mock quantity C')
                form.locator('[data-link-row]').first.locator('[name="leg_ref"]').select_option(leg_c_id)
                latest_leg = form.locator(f'[data-leg-row][data-draft-id="{leg_c_id}"]')
                latest_leg.locator('[name="occurred_time_timezone"]').select_option('America/New_York')
                latest_leg.locator('[name="occurred_time"]').fill('2024-11-03T01:30')
                form.locator('[data-review-preview]').click()
                expect(form.locator('[data-review-status]')).to_contain_text('不唯一')
                assert not previews
                latest_leg.locator('[name="occurred_time_timezone"]').select_option('Asia/Hong_Kong')
                latest_leg.locator('[name="occurred_time"]').fill('2024-01-01T08:00:03')
                page.evaluate("localStorage.setItem('paam.timezone','Asia/Tokyo')")
                form.locator('[data-review-preview]').click()
                expect(form.locator('[data-review-command]')).to_be_enabled()
                intent = previews[-1]['new_reviews'][0]['parameters']
                assert [row['title'] for row in intent['new_positions']] == ['Mock principal object']
                assert all(row['new_position_index'] == 0 for row in intent['legs'])
                assert all(row['occurred_time'] == '2024-01-01T00:00:03.000Z' for row in intent['legs'])
                assert [(row['allocation_index'], row['leg_index']) for row in intent['position_allocations']] == [(1, 1), (0, 0)]
                assert 'draft-' not in str(intent)
                expect(form.locator('[data-review-impact]')).to_contain_text('Mock draft person')
                expect(form.locator('[data-review-impact]')).to_contain_text('Mock principal cash')
                viewport_evidence(page, 'fix-r09-stable-draft-preview')
                principal = form.locator('[data-position-draft]')
                principal.locator('[data-remove]').click()
                form.locator('[data-review-preview]').click()
                expect(form.locator('[data-review-status]')).to_contain_text('请选择明确的数量对象')
                assert len(previews) == 1
                assert not errors and not commands, (errors, commands)
                browser.close()
            with target_database.SessionLocal() as db:
                after = [db.scalar(select(func.count()).select_from(model)) for model in (TransactionFact, ReviewCase, LedgerEntry)]
                assert after == before
            print('PASS named stable drafts: reorder/remove, retained links, rejected dangling target, explicit time zone/DST, valid real preview; zero publications')
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            from backend.core import target_database
            target_database.engine.dispose()


if __name__ == '__main__':
    run()
