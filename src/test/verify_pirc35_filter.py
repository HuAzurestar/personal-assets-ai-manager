"""Real page filters: density, named scopes, explicit zero, chips and navigation."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright

from browser_artifact import viewport_evidence
from browser_list import assert_full_list_text, assert_list_readability
from serve_m2_ui import prepare_app


def assert_common_row(page, form, currency_name, evidence):
    for width in (1440, 1280, 1100, 820, 390, 320):
        page.set_viewport_size({'width': width, 'height': 900})
        expect(form.locator('[name="word"]')).to_be_visible()
        expect(form.locator('[data-filter-more]')).not_to_have_attribute('open', '')
        row = page.locator('#page-content .detail-data-table tbody tr').first
        assert_list_readability(page, row, row.locator('.detail-primary strong'),
            row.locator('.detail-primary small'), amount=row.locator('.fact-amount'), max_height=90)
        long_title = page.locator('#page-content .detail-primary strong').filter(has_text='Mock filter Reserve')
        assert_full_list_text(long_title, '这是一条完全虚构的长中文账单摘要用于验证来源与业务内容不会被省略号隐藏')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
        if width > 1100:
            common = [form.locator(selector).bounding_box() for selector in
                ('[name="word"]', '[data-action="range-open"]', '[name="cash_direction"]',
                 f'[name="{currency_name}"]', 'button[type="submit"]', '[data-filter-more-toggle]')]
            assert max(box['y'] + box['height'] for box in common) - min(box['y'] + box['height'] for box in common) < 2, (width, common)
            assert common[0]['width'] >= 300, (width, common)
            assert form.bounding_box()['height'] <= 100, (width, form.bounding_box())
        if width <= 600:
            assert form.bounding_box()['height'] <= 290, (width, form.bounding_box())
            for button in form.locator('.transaction-filter-actions button').all():
                assert button.bounding_box()['height'] >= 44
        viewport_evidence(page, f'{evidence}-{width}')
    page.set_viewport_size({'width': 1440, 'height': 900})


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
                amount=1000, currency_code='CNY', account_code='', summary=f'Mock filter {name}' + (
                    ' 这是一条完全虚构的长中文账单摘要用于验证来源与业务内容不会被省略号隐藏' if name == 'Reserve' else ''))
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
            before_tables = {table.name: tuple(tuple(row) for row in db.execute(select(table).order_by(table.c.id)))
                for table in target_database.TargetBase.metadata.sorted_tables}
            assert len(before_tables) == 20
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
                assert_common_row(page, form, 'currency_code', 'dev17-common-fact-filter')
                viewport_evidence(page, 'fix-r12-fact-density')
                common_word = form.locator('[name="word"]').bounding_box()
                common_currency = form.locator('[name="currency_code"]').bounding_box()
                assert abs(common_word['y'] + common_word['height'] - common_currency['y'] - common_currency['height']) < 2, (common_word, common_currency)
                assert form.bounding_box()['height'] <= 100
                assert form.locator('[name="word"]').bounding_box()['width'] >= 300
                assert form.bounding_box()['height'] <= 150
                assert form.locator('input[type="number"]').count() == 0
                expect(form.locator('[data-filter-more]')).not_to_have_attribute('open', '')
                assert form.locator('.transaction-filter-currency').evaluate('(node) => getComputedStyle(node).whiteSpace') == 'nowrap'
                page.set_viewport_size({'width': 1280, 'height': 800})
                assert form.locator('[name="word"]').bounding_box()['width'] >= 300
                assert form.bounding_box()['height'] <= 150
                page.set_viewport_size({'width': 1440, 'height': 900})
                form.locator('[data-filter-more-toggle]').focus()
                form.locator('[data-filter-more-toggle]').press('Enter')
                expect(form.locator('[data-filter-more]')).to_have_attribute('open', '')
                form.locator('[data-filter-more-toggle]').press('Enter')
                expect(form.locator('[data-filter-more]')).not_to_have_attribute('open', '')
                # Search field is advanced but its applied meaning stays visible.
                form.locator('[data-filter-more-toggle]').click()
                form.locator('[name="search_field"]').select_option('counterparty_name')
                expect(form.locator('[data-search-label]')).to_have_text('交易对手搜索')
                expect(form.locator('[name="word"]')).to_have_attribute('placeholder', '搜索交易对手（字面匹配）')
                page.reload()
                expect(form.locator('[data-search-label]')).to_have_text('交易对手搜索')
                expect(form.locator('[name="search_field"]')).to_have_value('counterparty_name')
                expect(form.locator('[data-filter-more]')).not_to_have_attribute('open', '')
                form.locator('[data-action="detail-clear"]').click()
                expect(form.locator('[data-search-label]')).to_have_text('摘要搜索')
                # Observe the real five-second background timer while an
                # unsubmitted input is blurred; it must not erase the query.
                prior = len([url for url in reads if '/transaction_fact/list?' in url])
                form.locator('[name="word"]').fill('Mock unsent query')
                form.locator('[data-filter-more-toggle]').focus()
                page.wait_for_timeout(5500)
                expect(form.locator('[name="word"]')).to_have_value('Mock unsent query')
                assert len([url for url in reads if '/transaction_fact/list?' in url]) == prior
                with page.expect_request('**/paam/ledger/v1/transaction_fact/list?*'):
                    form.locator('[data-action="detail-clear"]').click()
                expect(page.locator('#page-content')).not_to_have_attribute('aria-busy', 'true')
                expect(form.locator('[name="word"]')).to_have_value('')

                def choose(name, word, identifier):
                    current = page.locator('[data-transaction-filter]')
                    current.locator('[data-filter-more-toggle]').click()
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
                with page.expect_request('**/paam/ledger/v1/transaction_fact/list?*'):
                    page.locator('[data-action="detail-clear"]').click()
                expect(page.locator('#page-content')).not_to_have_attribute('aria-busy', 'true')
                current = page.locator('[data-transaction-filter]')
                current.locator('[data-filter-more-toggle]').click()
                current.locator('[data-filter-zero="account_ref_id"]').click()
                expect(page.locator('[name="account_ref_id"]')).to_have_value('0')
                expect(page.locator('[data-filter-chip="account_ref_id"]')).to_contain_text('来源未识别')
                page.locator('[data-action="detail-clear"]').click()
                current.locator('[data-filter-more-toggle]').click()
                current.locator('[data-filter-zero="account_id"]').click()
                expect(page.locator('[name="account_id"]')).to_have_value('0')
                expect(page.locator('[data-action="fact-detail"]')).to_have_count(1)
                page.locator('[data-action="detail-clear"]').click()
                current.locator('[name="word"]').fill('Mock filter')
                current.locator('[name="word"]').press('Enter')
                expect(page.locator('[data-action="fact-detail"]')).to_have_count(3)
                expect(page.locator('[data-fact-scan-status]')).to_contain_text('本次扫描结束')
                prior = len([url for url in reads if '/fact/search?' in url])
                with page.expect_request('**/paam/ledger/v1/fact/search?*'):
                    current.locator('button[type="submit"]').click()
                expect(page.locator('#page-content')).not_to_have_attribute('aria-busy', 'true')
                assert len([url for url in reads if '/fact/search?' in url]) > prior
                current.locator('[name="currency_code"]').select_option('CNY')
                expect(page.locator('[data-action="fact-detail"]')).to_have_count(3)
                current.locator('[name="cash_direction"]').select_option('IN')
                expect(page.locator('[data-action="fact-detail"]')).to_have_count(2)
                expect(page.locator('[data-filter-chip="currency_code"]')).to_contain_text('CNY')
                expect(page.locator('[data-filter-chip="cash_direction"]')).to_contain_text('收入')
                current.locator('[name="cash_direction"]').select_option('OUT')
                expect(page.locator('[data-action="fact-detail"]')).to_have_count(1)
                current.locator('[name="cash_direction"]').select_option('')
                expect(page.locator('[data-action="fact-detail"]')).to_have_count(3)
                for width in (1280, 390):
                    page.set_viewport_size({'width': width, 'height': 900})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                viewport_evidence(page, 'fix-r12-fact-mobile')
                page.set_viewport_size({'width': 1440, 'height': 900})
                # The actual calendar submits inclusive end-minute as a UTC
                # exclusive boundary and keeps page size until explicit clear.
                page.goto(base + '/#details/transaction-fact?date_from=2026-09-01T00%3A00&date_to=2026-09-01T23%3A59&page_size=50')
                expect(page.locator('[data-action="fact-detail"]')).to_have_count(1)
                page.locator('[data-action="range-open"]').click()
                page.locator('[data-range-date="2026-09-02"]').click()
                page.locator('[data-range-date="2026-09-02"]').click()
                with page.expect_request('**/paam/ledger/v1/transaction_fact/list?*') as reading:
                    page.locator('[data-range-apply]').click()
                predicate = json.loads(parse_qs(urlsplit(reading.value.url).query)['filter'][0])
                assert predicate == {'op': 'AND', 'expression': [
                    {'key': 'occurred_time', 'op': '>=', 'val': '2026-09-01T16:00:00.000Z'},
                    {'key': 'occurred_time', 'op': '<', 'val': '2026-09-02T16:00:00.000Z'}]}, predicate
                expect(page.locator('[data-action="fact-detail"]')).to_have_count(1)
                expect(page.locator('[data-action="fact-detail"]')).to_contain_text('Mock filter Reserve')
                expect(page.locator('[data-action="detail-page-size"]')).to_have_value('50')
                expect(page.locator('[data-filter-chip="date_range"]')).to_contain_text('2026-09-02')
                page.locator('[data-filter-chip="date_range"]').click()
                expect(page.locator('[data-filter-chip="date_range"]')).to_have_count(0)
                expect(page.locator('[data-action="detail-page-size"]')).to_have_value('50')
                page.locator('[data-action="detail-clear"]').click()
                expect(page.locator('[data-action="detail-page-size"]')).to_have_value('20')
                # A name read failure is not permission to clear the filter.
                def fail_name(route):
                    route.fulfill(status=503, content_type='application/json', body=
                        '{"status":503,"message":"Mock name read busy","body":{"code":"QUERY_BUSY"}}')
                name_pattern = f'**/paam/ledger/v1/account-party/{person_id}'
                page.route(name_pattern, fail_name, times=1)
                page.goto(base + f'/#details/transaction-fact?party_id={person_id}')
                expect(page.locator('[data-filter-chip="party_id"]')).to_contain_text('名称读取失败')
                expect(page.locator('[name="party_id"]')).to_have_value(str(person_id))
                expect(page.locator('[data-action="fact-detail"]')).to_have_count(1)
                page.reload()
                expect(page.locator('[data-filter-chip="party_id"]')).to_contain_text('Mock filter Alice')
                # Hold one name response across the real refresh interval.
                # Polling must not repeatedly abort/restart pending hydration.
                held = []
                actual_name = httpx.get(base + f'/paam/ledger/v1/account-party/{person_id}', trust_env=False).json()
                page.route(name_pattern, lambda route: held.append(route), times=1)
                page.goto(base + f'/#details/transaction-fact?party_id={person_id}')
                expect(page.locator('[data-filter-chip="party_id"]')).to_contain_text('正在读取名称')
                expect(page.locator('[data-action="fact-detail"]')).to_have_count(1)
                page.wait_for_timeout(5500)
                expect(page.locator('[data-filter-chip="party_id"]')).to_contain_text('正在读取名称')
                assert len(held) == 1
                held[0].fulfill(status=200, content_type='application/json', body=json.dumps(actual_name))
                expect(page.locator('[data-filter-chip="party_id"]')).to_contain_text('Mock filter Alice')
                # Changing route aborts and closes the open scoped picker.
                page.locator('[data-filter-more-toggle]').click()
                page.locator('[data-named-choice="party_id"] [data-choice-pick]').click()
                expect(page.locator('dialog[open]')).to_be_visible()
                page.evaluate("location.hash = '#details/ledger'")
                expect(page.locator('dialog[open]')).to_have_count(0)
                page.goto(base + '/#details/ledger')
                flow_form = page.locator('[data-form="economic-filter"]')
                expect(flow_form).to_be_visible()
                assert_common_row(page, flow_form, 'cash_currency_code', 'dev17-common-flow-filter')
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
                # Invalid deep links must not issue rounded identity/list reads.
                expect(page.locator('#page-content')).not_to_have_attribute('aria-busy', 'true')
                prior = len(reads)
                page.goto(base + '/#details/ledger?party_id=9007199254740993')
                expect(page.locator('#page-content')).to_contain_text('对象 ID')
                assert not any('/flow/list?' in url or '/account-party/' in url for url in reads[prior:])
                with target_database.SessionLocal() as db:
                    assert counts == [db.scalar(select(func.count()).select_from(entity))
                                      for entity in [TransactionFact, ReviewCase, LedgerEntry]]
                    after_tables = {table.name: tuple(tuple(row) for row in db.execute(select(table).order_by(table.c.id)))
                        for table in target_database.TargetBase.metadata.sorted_tables}
                    assert after_tables == before_tables, 'read-only filtering changed a business table'
                assert not writes and not errors, (writes, errors)
                browser.close()
            print('PASS compact Fact/Flow, named scopes, zero/clear, chips/reload/history, same-condition reread and bounded tag search; no writes/providers')
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            target_database.engine.dispose()


if __name__ == '__main__':
    run()
