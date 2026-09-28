"""Exercise simplified settings and responsive operations with fictional data."""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time

import httpx
from playwright.sync_api import expect, sync_playwright
import uvicorn

from serve_m2_ui import prepare_app


def run():
    with tempfile.TemporaryDirectory(prefix="paam-repair-ui-") as temp:
        app = prepare_app(Path(temp))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        try:
            for _ in range(100):
                try:
                    if httpx.get(base + '/api/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge" if os.name == 'nt' else None, headless=True)
                page = browser.new_page(viewport={"width":1440,"height":900}, locale="zh-CN")
                errors, writes, probes = [], [], []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.on('request', lambda r: writes.append(r.url) if r.method == 'PUT' else None)
                page.goto(base + '/#settings/automation')
                root = page.locator('[data-auto-page="settings"]')
                expect(root).to_be_visible()
                assert root.bounding_box()['height'] <= 700
                assert root.locator('button:visible').count() <= 6
                assert root.locator('[data-auto-runtime]').is_hidden()

                def current():
                    return page.request.get(base + '/paam/system/v1/setting/automation').json()['body']

                original = current()
                page.goto(base + '/#workbench/tag-review')
                page.locator('a[href^="#workbench/tag-review?request_id="]').first.click()
                expect(page.locator('[data-auto-request-detail]')).to_be_visible()
                page.goto(base + '/#settings/automation')
                page.locator('[data-action="model-edit"]').click()
                form = page.locator('[data-form="automation-model"]')
                expect(form.locator('[name="extras"]')).to_be_hidden()
                form.locator('[name="name"]').fill('简化配置验收')
                form.locator('button[type="submit"]').click()
                expect(page.locator('dialog[open]')).to_have_count(0)
                assert current()['models'][0]['litellm_params'] == original['models'][0]['litellm_params']
                assert current()['models'][0]['key_configured']

                # A stale/invalid configuration must not mutate credentials first.
                page.locator('[data-action="model-edit"]').click()
                form = page.locator('[data-form="automation-model"]')
                form.locator('[name="secret"]').fill('fictional-replacement-key')
                before_conflict = len(writes)
                page.route('**/setting/automation', lambda route: route.fulfill(
                    status=409, content_type='application/json',
                    body=json.dumps({'status':409, 'message':'Configuration changed', 'body':None}),
                ) if route.request.method == 'PUT' else route.continue_())
                form.locator('button[type="submit"]').click()
                expect(form.locator('.form-error-slot')).not_to_be_empty()
                assert not any(url.endswith('/secret') for url in writes[before_conflict:])
                page.unroute('**/setting/automation')
                page.locator('dialog[open] [data-close]').first.click()

                def answer_probe(route):
                    probes.append(route.request.post_data_json)
                    route.fulfill(status=200, content_type='application/json', body=json.dumps({
                        'status':200, 'message':'ok', 'body': {
                            'model_id':1, 'connected':True, 'mode':'LIVE', 'code':'CONNECTED',
                            'message':'虚构 HTTP 响应，仅验证界面', 'checked_at':'2026-09-27T00:00:00Z',
                            'configuration_updated_time':current()['updated_time'],
                        },
                    }))

                page.route('**/model/1/connection_check', answer_probe)
                page.locator('[data-action="model-test"]').click()
                assert probes == []
                confirm = page.locator('[data-confirm-connection]')
                confirm.evaluate('(el) => { el.click(); el.click(); }')
                expect(page.locator('[data-connection-feedback]')).to_contain_text('连接成功')
                assert len(probes) == 1 and probes[0]['confirmed'] is True
                page.locator('dialog[open] [data-close]').first.click()

                page.locator('[data-action="disclosure-edit"]').click()
                count = len(writes)
                page.locator('[data-disclosure-form] button[type="submit"]').click()
                expect(page.locator('dialog[open]')).to_have_count(0)
                assert len(writes) == count, 'unchanged form must not reset pending work'
                page.locator('[data-action="disclosure-edit"]').click()
                form = page.locator('[data-disclosure-form]')
                form.locator('[data-boundary]').nth(1).fill('30.01')
                expect(form.locator('[data-band-preview]')).to_contain_text('30.01')
                form.locator('[name="acknowledged"]').check()
                form.locator('button[type="submit"]').click()
                expect(page.locator('dialog[open]')).to_have_count(0)
                assert current()['disclosure']['amount_bands']['CNY']['boundaries'][1] == 3001
                assert current()['disclosure']['date_granularity'] == original['disclosure']['date_granularity']

                page.goto(base + '/#details/auto-rule')
                page.locator('[data-action="rule-edit"]').first.click()
                form = page.locator('[data-form="automation-rule"]')
                form.locator('[name="frequency"]').select_option('day')
                form.locator('[name="daily_time"]').fill('09:30')
                form.locator('button[type="submit"]').click()
                expect(page.locator('dialog[open]')).to_have_count(0)
                rule_id = page.locator('[data-rule-row]').first.get_attribute('data-rule-row')
                saved_rule = page.request.get(base + f'/paam/tag/v1/auto_rule/{rule_id}').json()['body']
                assert saved_rule['cron'] == '30 9 * * *'
                page.locator('[data-action="rule-edit"]').first.click()
                expect(page.locator('[name="frequency"]')).to_have_value('day')
                expect(page.locator('[name="daily_time"]')).to_have_value('09:30')
                page.locator('dialog[open] [data-close]').first.click()

                # No table or modal may put the primary operations off-screen.
                for width, height in [(390,844),(768,1024),(1440,900)]:
                    page.set_viewport_size({'width':width,'height':height})
                    for route, selector in [('settings/automation','settings'),('details/auto-rule','rules'),('workbench/tag-review','requests')]:
                        page.goto(base + '/#' + route)
                        page.locator(f'[data-auto-page="{selector}"]').wait_for()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width,route)
                        if selector == 'requests':
                            expect(page.locator('[data-tag-request-select-all]')).to_be_visible()
                        for button in page.locator('[data-auto-page] button:visible').all():
                            box = button.bounding_box()
                            assert box['x'] >= 0 and box['x'] + box['width'] <= width + 1, (width,route,button.inner_text())
                    page.goto(base + '/#settings/automation')
                    page.locator('[data-action="model-edit"]').click()
                    box = page.locator('dialog[open]').bounding_box()
                    assert box['x'] >= 0 and box['x'] + box['width'] <= width + 1
                    page.locator('dialog[open] [data-close]').first.click()
                assert errors == [], errors
                browser.close()
            print('PASS compact settings, unchanged advanced values, explicit single probe, exact amount form, no-op save, frequency roundtrip, 3 viewport operations; provider calls=0')
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            from backend.core import target_database
            target_database.engine.dispose()


if __name__ == '__main__':
    run()
