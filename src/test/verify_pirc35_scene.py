"""Actual scene-specific editing, destructive switch approval and frozen preview."""
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
from browser_choice import choose_local


def run():
    with tempfile.TemporaryDirectory(prefix='paam-pirc35-scene-') as temporary:
        app = prepare_app(Path(temporary))
        from backend.core import target_database
        from backend.entity import TransactionFact
        from backend.mapper.review_command_mapper import ReviewCommandMapper
        with target_database.SessionLocal() as db:
            db.add(TransactionFact(id=3, fact_key='mock-scene-cash',
                occurred_time=datetime(2024, 1, 1, tzinfo=timezone.utc), cash_direction=2,
                amount=40000, currency_code='CNY', account_code='', summary='Mock scene cash'))
            db.flush()
            ReviewCommandMapper(db).create_initial_defaults([3])
            db.commit()
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
                response = client.post('/paam/ledger/v1/account-party', json={'name': 'Mock scene owner'})
                assert response.status_code == 200, response.text
                party_id = response.json()['body']['id']
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='msedge' if os.name == 'nt' else None, headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 900})
                errors, previews, commands, candidate_reads = [], [], [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: previews.append(request.post_data_json)
                    if request.method == 'POST' and request.url.endswith('/review/preview')
                    else commands.append(request.post_data_json) if request.method == 'POST' and request.url.endswith('/review/command') else None)
                page.on('request', lambda request: candidate_reads.append(request.url)
                    if '/paam/ledger/v1/candidate/' in request.url else None)
                page.goto(base + '/#workbench/review?facts=3')
                form = page.locator('[data-immutable-review]')
                expect(form.locator('[data-cash-row]')).to_have_count(1)
                expect(form.locator('[data-review-step]')).to_have_count(3)
                expect(form.locator('[data-fact-picker]')).to_be_hidden()
                expect(form.locator('[data-scene-selection-summary]')).to_contain_text('已选 1 个完整事实')
                for area in ('phase', 'quantity', 'link', 'duplicate'):
                    expect(form.locator(f'[data-scene-{area}]')).to_be_hidden()
                expect(form.locator('[data-scene-cash]')).to_be_visible()
                expect(form.locator('[data-cash-row]')).to_be_hidden()
                # Ordinary source correction/splitting stays available, but is
                # no longer compulsory scaffolding for every selected Fact.
                form.locator('[data-scene-cash-summary]').click()
                expect(form.locator('[data-cash-row]')).to_be_visible()
                form.locator('[data-scene-cash-summary]').click()
                expect(form.locator('[data-cash-row] [name="economic_type"] option')).to_have_count(1)
                form.locator('[name="title"]').fill('Mock simple publication')
                viewport_evidence(page, 'fix-r07-normal-scene')
                form.locator('[data-review-preview]').click()
                expect(form.locator('[data-review-command]')).to_be_enabled()
                expect(form.locator('[data-financial-scope-note]')).to_contain_text('不代表业务已核对正确')
                business = form.locator('[data-review-business]')
                expect(business.locator('[data-review-cash-result]')).to_have_count(1)
                expect(business.locator('[data-review-cash-result]')).to_contain_text('支出')
                expect(business.locator('[data-review-cash-result]')).to_contain_text('400.00 CNY')
                assert business.locator('[data-review-technical]').get_attribute('open') is None
                for width in (1280, 820, 390):
                    page.set_viewport_size({'width':width,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    cash_box = business.locator('[data-review-cash-result]').bounding_box()
                    assert cash_box['height'] <= (160 if width == 390 else 90), cash_box
                    assert cash_box['y'] < business.locator('[data-review-technical]').bounding_box()['y']
                    viewport_evidence(page,f'dev17-review-business-normal-{width}')
                page.set_viewport_size({'width':1440,'height':900})
                first = previews[-1]['new_reviews'][0]
                assert first['case_code'] == 'NORMAL'
                assert first['parameters']['legs'] == first['parameters']['new_positions'] == first['parameters']['position_allocations'] == []
                form.locator('[data-review-command]').click()
                expect(form).to_have_count(0)
                assert len(commands) == 1

                # Every preset exposes only applicable sections. Complex ones
                # retain an explicit optional duplicate-evidence disclosure.
                page.goto(base + '/#workbench/review?facts=3&case_code=BORROW_REPAY')
                expect(form.locator('[data-cash-row]')).to_have_count(1)
                for case in ('REFUND', 'INTERNAL_TRANSFER', 'DUPLICATE', 'SHARED_PAYMENT',
                             'POS_POSITION_OPEN', 'POS_POSITION_SETTLE', 'POS_CREDIT_PURCHASE', 'POS_CREDIT_REPAY', 'BORROW_REPAY'):
                    form.locator('[name="case_code"]').select_option(case)
                    simple = case in ('REFUND', 'INTERNAL_TRANSFER', 'DUPLICATE')
                    expect(form.locator('[data-scene-quantity]')).to_be_hidden() if simple else expect(form.locator('[data-scene-quantity]')).to_be_visible()
                    expect(form.locator('[data-scene-link]')).to_be_hidden() if simple else expect(form.locator('[data-scene-link]')).to_be_visible()
                    expect(form.locator('[data-scene-phase]')).to_be_visible() if case == 'SHARED_PAYMENT' else expect(form.locator('[data-scene-phase]')).to_be_hidden()
                    if case == 'DUPLICATE':
                        expect(form.locator('[data-add-duplicate]')).to_be_visible()
                    elif not simple:
                        expect(form.locator('[data-add-duplicate]')).to_be_hidden()
                        form.locator('[data-scene-duplicate] summary').click()
                        expect(form.locator('[data-add-duplicate]')).to_be_visible()
                        form.locator('[data-scene-duplicate] summary').click()

                form.locator('[data-new-position]').click()
                draft = form.locator('[data-position-draft]')
                draft.locator('[name="title"]').fill('Mock scene principal')
                draft.locator('[name="usage_scenario"]').select_option('PERSONAL-LENDING')
                draft.locator('[data-pick-party]').click()
                page.locator(f'dialog[open] [data-party-picker] [data-picker-id="{party_id}"]').click()
                form.locator('[data-add-leg]').click()
                leg = form.locator('[data-leg-row]')
                leg.locator('[name="leg_amount"]').fill('400')
                leg.locator('[name="occurred_time_timezone"]').select_option('UTC')
                leg.locator('[name="occurred_time"]').fill('2024-01-01T00:00')
                form.locator('[data-add-link]').click()
                link = form.locator('[data-link-row]')
                choose_local(page, link, 'allocation_ref', form.locator('[data-cash-row]').get_attribute('data-draft-id'))
                choose_local(page, link, 'leg_ref', leg.get_attribute('data-draft-id'))
                link.locator('[name="cash_amount"]').fill('400')
                form.locator('[data-review-preview]').click()
                expect(form.locator('[data-review-command]')).to_be_enabled()
                expect(business.locator('[data-review-quantity-change]')).to_contain_text('Mock scene principal')
                expect(business.locator('[data-review-quantity-change]')).to_contain_text('数量未知')
                expect(business.locator('[data-review-quantity-change]')).to_contain_text('400.00 CNY')
                for width in (1280, 820, 390):
                    page.set_viewport_size({'width':width,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    expect(business.locator('[data-review-quantity-change]')).to_contain_text('UNKNOWN')
                    assert business.locator('[data-review-technical]').get_attribute('open') is None
                    viewport_evidence(page,f'dev17-review-business-principal-{width}')
                page.set_viewport_size({'width':1440,'height':900})
                viewport_evidence(page, 'fix-r07-principal-scene')
                form.locator('[name="case_code"]').select_option('NORMAL')
                switch = page.locator('dialog[open] [data-scene-switch]')
                expect(switch).to_contain_text('1 个新对象')
                switch.locator('[data-scene-cancel]').click()
                expect(form.locator('[name="case_code"]')).to_have_value('BORROW_REPAY')
                expect(draft).to_have_count(1)
                expect(leg).to_have_count(1)
                expect(link).to_have_count(1)
                expect(form.locator('[data-review-command]')).to_be_disabled()
                form.locator('[name="case_code"]').select_option('NORMAL')
                switch.locator('[data-scene-apply]').click()
                for selector in ('[data-position-draft]', '[data-leg-row]', '[data-link-row]'):
                    expect(form.locator(selector)).to_have_count(0)
                expect(form.locator('[data-cash-row] [name="economic_type"]')).to_have_value('TRANSACTION')
                form.locator('[data-review-preview]').click()
                expect(form.locator('[data-review-command]')).to_be_enabled()
                clean = previews[-1]['new_reviews'][0]
                assert clean['parameters']['legs'] == clean['parameters']['new_positions'] == clean['parameters']['position_allocations'] == []
                assert clean['duplicate_transactions'] == clean['account_bindings'] == []
                assert len(commands) == 1, 'scene changes must never publish automatically'

                # An optional duplicate draft stays visibly disclosed when
                # moving to a compatible quantity scene; moving to NORMAL
                # requires approval before clearing it.
                form.locator('[name="case_code"]').select_option('DUPLICATE')
                form.locator('[data-add-duplicate]').click()
                form.locator('[name="case_code"]').select_option('BORROW_REPAY')
                expect(form.locator('[data-duplicate-row]')).to_be_visible()
                expect(form.locator('[data-scene-duplicate] summary')).to_contain_text('1 项')
                form.locator('[name="case_code"]').select_option('NORMAL')
                expect(switch).to_contain_text('1 条重复证据')
                switch.locator('[data-scene-apply]').click()
                expect(form.locator('[data-duplicate-row]')).to_have_count(0)
                # Hidden stale transport input is rejected before a preview
                # request, rather than silently omitted from a different intent.
                form.locator('[data-leg-rows]').evaluate('node => {const row=document.createElement("article"); row.dataset.legRow=""; node.append(row);}')
                before = len(previews)
                form.locator('[data-review-preview]').click()
                expect(form.locator('[data-review-status]')).to_contain_text('不适用于当前场景')
                assert len(previews) == before
                form.locator('[data-leg-rows]').evaluate('node => node.replaceChildren()')

                form.locator('[name="case_code"]').select_option('POS_OPENING')
                expect(switch).to_contain_text('1 个完整事实')
                switch.locator('[data-scene-apply]').click()
                expect(form.locator('[data-scene-cash-selection]')).to_be_hidden()
                expect(form.locator('[data-scene-cash]')).to_be_hidden()
                expect(form.locator('[data-cash-row]')).to_have_count(0)
                expect(form.locator('[data-scene-link]')).to_be_hidden()
                expect(form.locator('[data-scene-quantity]')).to_be_visible()
                # Re-entering cash mode lazily restores the actual picker.
                form.locator('[name="case_code"]').select_option('NORMAL')
                form.locator('[data-fact-picker] [data-picker-id="3"]').click()
                expect(form.locator('[data-cash-row]')).to_have_count(1)
                form.locator('[data-scene-selection-summary]').click()
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                expect(page.locator('.toast')).to_have_count(0, timeout=6000)
                viewport_evidence(page, 'fix-r07-normal-mobile')
                assert form.locator('[data-review-preview]').bounding_box()['y'] < 844
                # Source and split details still work at narrow widths.
                form.locator('[data-scene-cash-summary]').click()
                expect(form.locator('[data-cash-row]')).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                form.locator('[data-review-preview]').click()
                expect(form.locator('[data-review-command]')).to_be_enabled()
                expect(business.locator('[data-review-cash-result]')).to_be_visible()
                assert business.locator('[data-review-technical]').get_attribute('open') is None
                viewport_evidence(page, 'fix-r07-normal-mobile-preview')
                preview_top = form.locator('[data-review-step="preview"]').bounding_box()['y']
                # scrollIntoView rounds scrollY to whole pixels, while CSS
                # geometry retains fractions (observed -0.46875px). Require
                # actual top alignment within one pixel, not a loose visibility
                # bound or exact-zero integer assumption.
                assert abs(preview_top) <= 1, {'preview_top': preview_top, 'scroll': page.evaluate('window.scrollY')}
                assert len(commands) == 1
                before = len(candidate_reads)
                page.goto(base + '/#workbench/review?case_code=POS_OPENING')
                expect(form.locator('[data-scene-quantity]')).to_be_visible()
                expect(form.locator('[data-fact-picker] [data-picker-id]')).to_have_count(0)
                assert len(candidate_reads) == before, 'pure quantity must not load hidden cash candidates'
                page.goto(base + '/#workbench/review?case_code=POS_OPENING&facts=3')
                expect(page.locator('#page-content')).to_contain_text('期初数量场景不能携带现金事实')
                expect(form).to_have_count(0)
                page.goto(base + '/#workbench/review?case_code=not-a-preset')
                expect(page.locator('#page-content')).to_contain_text('不支持的业务场景')
                assert errors == [], errors
                browser.close()
            from sqlalchemy import func, select
            from backend.entity import Position, PositionLeg, ReviewCase, LedgerEntry
            with target_database.SessionLocal() as db:
                assert db.scalar(select(func.count()).select_from(Position)) == 0
                assert db.scalar(select(func.count()).select_from(PositionLeg)) == 0
                assert db.scalar(select(func.count()).select_from(ReviewCase)) == 4  # three defaults + one NORMAL
                assert db.scalar(select(func.count()).select_from(LedgerEntry)) == 4
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            target_database.engine.dispose()


if __name__ == '__main__':
    run()
