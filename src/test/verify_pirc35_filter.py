"""Real page filters: density, named scopes, explicit zero, chips and navigation."""
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
    with tempfile.TemporaryDirectory(prefix='paam-filter-browser-') as temporary:
        app = prepare_app(Path(temporary))
        from backend.core import target_database
        from backend.entity import (LedgerAccountParty, LedgerAccount, LedgerAccountRef,
            TransactionFact, ReviewCase, LedgerEntry, TargetTag)
        from backend.mapper.review_command_mapper import ReviewCommandMapper
        from sqlalchemy import select, func
        with target_database.SessionLocal() as db:
            person = LedgerAccountParty(name='Mock filter Alice')
            db.add(person)
            db.flush()
            group = LedgerAccount(name='Mock filter daily', party_id=person.id)
            db.add(group)
            db.flush()
            refs = [LedgerAccountRef(account_id=group.id, institution='Mock bank', name='Mock filter card',
                source_namespace='ccb:statement-v1', source_identity='9900990000001234', identity_strength=1),
                LedgerAccountRef(account_id=0, institution='Mock reserve', name='Mock filter ungrouped')]
            db.add_all(refs)
            db.flush()
            rows = [TransactionFact(fact_key=f'mock-filter-{name}',
                occurred_time=datetime(2026, 9, day, tzinfo=timezone.utc), cash_direction=direction,
                amount=1000, currency_code='CNY', account_code='', summary=f'Mock filter {name}')
                for name, day, direction in [('Alpha', 1, 1), ('Reserve', 2, 2), ('Unknown', 3, 1)]]
            db.add_all(rows)
            db.flush()
            ReviewCommandMapper(db).create_initial_defaults([row.id for row in rows],
                account_refs={rows[0].id: refs[0].id, rows[1].id: refs[1].id})
            tags = [TargetTag(view_id=1, name=f'Mock filter tag {i}', system_name=f'mock-filter-{i}')
                    for i in range(45)]
            tags[-1].name = 'Mock late filter tag'
            db.add_all(tags)
            db.commit()
            person_id, group_id, ref_id, tag_id = person.id, group.id, refs[0].id, tags[-1].id
            counts = [db.scalar(select(func.count()).select_from(entity))
                      for entity in [TransactionFact, ReviewCase, LedgerEntry]]
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
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='msedge' if os.name == 'nt' else None, headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 900})
                errors, writes, reads = [], [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda req: (reads if req.method in ['GET', 'HEAD'] else writes).append(req.url))
                page.goto(base + '/#details/transaction-fact')
                form = page.locator('[data-form="fact-filter"]')
                expect(form.locator('[name="word"]')).to_be_visible()
                viewport_evidence(page, 'fix-r12-fact-density')
                assert form.locator('[name="word"]').bounding_box()['width'] >= 300
                assert form.bounding_box()['height'] <= 150
                assert form.locator('input[type="number"]').count() == 0
                expect(form.locator('[data-filter-more]')).not_to_have_attribute('open', '')

                def choose(name, word, identifier):
                    current = page.locator('[data-transaction-filter]')
                    current.locator('[data-filter-more] > summary').click()
                    current.locator(f'[data-named-choice="{name}"] [data-choice-pick]').click()
                    picker = page.locator('dialog[open]')
                    picker.locator('[data-picker-word]').fill(word)
                    picker.locator('[data-picker-search]').click()
                    picker.locator(f'[data-picker-id="{identifier}"]').click()
                    expect(page.locator('[data-transaction-filter]')).to_be_visible()
                    expect(page.locator(f'[data-filter-chip="{name}"]')).to_contain_text(word)

                choose('party_id', 'Mock filter Alice', person_id)
                expect(page.locator('[data-action="fact-detail"]')).to_have_count(1)
                expect(page.locator('[data-filter-chip="party_id"]')).to_contain_text('Mock filter Alice')
                # Reload hydrates the name from the ID, without dropping scope.
                page.reload()
                expect(page.locator('[data-filter-chip="party_id"]')).to_contain_text('Mock filter Alice')
                page.locator('[data-filter-chip="party_id"]').click()
                expect(page.locator('[data-filter-chip="party_id"]')).to_have_count(0)
                choose('account_id', 'Mock filter daily', group_id)
                expect(page.locator('[data-action="fact-detail"]')).to_have_count(1)
                choose('account_ref_id', 'Mock filter card', ref_id)
                expect(page.locator('[data-action="fact-detail"]')).to_have_count(1)
                page.locator('[data-filter-chip="account_id"]').click()
                expect(page.locator('[data-filter-chip="account_ref_id"]')).to_contain_text('Mock filter card')
                # URL/back restores the exact remaining named scope.
                page.go_back()
                expect(page.locator('[data-filter-chip="account_id"]')).to_contain_text('Mock filter daily')
                page.go_forward()
                expect(page.locator('[data-filter-chip="account_id"]')).to_have_count(0)
                expect(page.locator('[data-filter-chip="account_ref_id"]')).to_contain_text('Mock filter card')
                page.locator('[data-action="detail-clear"]').click()
                expect(page.locator('[data-filter-chip]')).to_have_count(0)
                current = page.locator('[data-transaction-filter]')
                current.locator('[data-filter-more] > summary').click()
                current.locator('[data-filter-zero="account_ref_id"]').click()
                expect(page.locator('[name="account_ref_id"]')).to_have_value('0')
                expect(page.locator('[data-filter-chip="account_ref_id"]')).to_contain_text('来源未识别')
                page.locator('[data-action="detail-clear"]').click()
                current.locator('[data-filter-more] > summary').click()
                current.locator('[data-filter-zero="account_id"]').click()
                expect(page.locator('[name="account_id"]')).to_have_value('0')
                expect(page.locator('[data-action="fact-detail"]')).to_have_count(1)
                page.locator('[data-action="detail-clear"]').click()
                current.locator('[name="word"]').fill('Mock filter')
                current.locator('[name="word"]').press('Enter')
                expect(page.locator('[data-action="fact-detail"]')).to_have_count(3)
                expect(page.locator('[data-fact-read] [data-scan-progress="fact"]')).to_contain_text('扫描完成')
                prior = len([url for url in reads if '/fact/search?' in url])
                with page.expect_request('**/paam/ledger/v1/fact/search?*'):
                    current.locator('button[type="submit"]').click()
                expect(page.locator('#page-content')).to_have_attribute('aria-busy', 'false')
                assert len([url for url in reads if '/fact/search?' in url]) > prior
                for width in (1280, 390):
                    page.set_viewport_size({'width': width, 'height': 900})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                viewport_evidence(page, 'fix-r12-fact-mobile')
                page.set_viewport_size({'width': 1440, 'height': 900})
                page.goto(base + '/#details/ledger')
                flow_form = page.locator('[data-form="economic-filter"]')
                expect(flow_form).to_be_visible()
                assert flow_form.locator('[name="word"]').bounding_box()['width'] >= 300
                assert flow_form.bounding_box()['height'] <= 150
                choose('tag_id', 'Mock late filter tag', tag_id)
                expect(page.locator('[data-action="economic-detail"]')).to_have_count(0)
                assert any('/tag/v1/tag/search?' in url for url in reads)
                page.reload()
                expect(page.locator('[data-filter-chip="tag_id"]')).to_contain_text('Mock late filter tag')
                viewport_evidence(page, 'fix-r12-flow-named-tag')
                page.locator('[data-action="detail-clear"]').click()
                expect(page.locator('[data-filter-chip]')).to_have_count(0)
                with target_database.SessionLocal() as db:
                    assert counts == [db.scalar(select(func.count()).select_from(entity))
                                      for entity in [TransactionFact, ReviewCase, LedgerEntry]]
                assert not writes and not errors, (writes, errors)
                browser.close()
            print('PASS compact Fact/Flow, named scopes, zero/clear, chips/reload/history, same-condition reread and bounded tag search; no writes/providers')
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            target_database.engine.dispose()


if __name__ == '__main__':
    run()
