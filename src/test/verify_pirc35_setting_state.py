"""Four independent setting observations: real reads and injected GET failures."""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time

import httpx
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select
import uvicorn

from serve_m2_ui import prepare_app
from browser_artifact import viewport_evidence


def run():
    with tempfile.TemporaryDirectory(prefix='paam-setting-state-') as temporary:
        app = prepare_app(Path(temporary))
        from backend.core import target_database
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
                def snapshot():
                    with target_database.SessionLocal() as db:
                        return {table.name: tuple(tuple(row) for row in db.execute(select(table).order_by(table.c.id)))
                                for table in target_database.TargetBase.metadata.sorted_tables}
                before = snapshot()
                assert len(before) == 20
                actual_setting = client.get('/paam/system/v1/setting/automation').json()['body']
                actual_schedule = client.get('/paam/system/v1/schedule/status').json()['body']
                with sync_playwright() as p:
                    browser = p.chromium.launch(channel='msedge' if os.name == 'nt' else None, headless=True)
                    page = browser.new_page(viewport={'width':1280, 'height':900})
                    errors, mutations = [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: mutations.append(request.url) if request.method not in ('GET','HEAD') else None)
                    def card(name):
                        return page.locator(f'[data-automation-state="{name}"]')
                    def load():
                        page.goto(base + '/#settings/automation')
                        expect(card('service')).to_be_visible()
                        expect(page.locator('[data-auto-states] article')).to_have_count(4)
                    load()
                    expect(card('service')).to_have_attribute('data-state', 'connected')
                    expect(card('model')).to_contain_text('不代表真实连接已验证')
                    expect(card('model')).to_contain_text(f"{len(actual_setting['models'])} 个配置")
                    expect(card('analysis')).to_have_attribute('data-state', 'unavailable')
                    expect(card('task')).to_contain_text(f"已注册 {len(actual_schedule['tasks'])}")
                    for width in (1280, 820, 390):
                        page.set_viewport_size({'width':width, 'height':900})
                        card('service').scroll_into_view_if_needed()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                        for name in ('service','model','analysis','task'):
                            rect = card(name).bounding_box()
                            assert rect and rect['x'] >= 0 and rect['x'] + rect['width'] <= width + 1, rect
                        viewport_evidence(page, f'setting-states-{width}')
                    # Known settings toggle off does not imply service/scheduler off.
                    setting_path = '**/paam/system/v1/setting/automation'
                    page.route(setting_path, lambda route: route.fulfill(json={'status':200,'message':'ok',
                        'body':actual_setting | {'scan_available':True,'scan_enabled':False}}))
                    load()
                    expect(card('service')).to_have_attribute('data-state','connected')
                    expect(card('analysis')).to_have_attribute('data-state','off')
                    expect(page.locator('[data-auto-notice]')).to_contain_text('自动分析已关闭')
                    expect(page.locator('[data-action="scan-toggle"]')).to_be_enabled()
                    # Partial polling failure updates the affected card, retains health/config.
                    schedule_path = '**/paam/system/v1/schedule/status'
                    page.route(schedule_path, lambda route: route.abort())
                    expect(card('task')).to_have_attribute('data-state','unknown', timeout=15000)
                    expect(card('task')).not_to_contain_text('已注册 0')
                    expect(card('service')).to_have_attribute('data-state','connected')
                    expect(card('analysis')).to_have_attribute('data-state','off')
                    expect(page.locator('[data-auto-freshness]')).to_contain_text('部分状态读取失败')
                    page.unroute(schedule_path)
                    page.route(schedule_path, lambda route: route.fulfill(json={'status':200,'message':'ok',
                        'body':actual_schedule | {'scheduler_state':'STOPPED','worker_state':'UNHEALTHY','tasks':[]}}))
                    expect(card('task')).to_contain_text('调度停止 · 执行器异常', timeout=15000)
                    expect(card('service')).to_have_attribute('data-state','connected')
                    page.unroute(schedule_path)
                    # Initial missing setting still shows the other independent observations.
                    page.unroute(setting_path)
                    page.route(setting_path, lambda route: route.abort())
                    load()
                    expect(card('model')).to_have_attribute('data-state','unknown')
                    expect(card('analysis')).to_have_attribute('data-state','unknown')
                    expect(card('service')).to_have_attribute('data-state','connected')
                    expect(page.locator('[data-action="scan-toggle"]')).to_be_disabled()
                    expect(page.locator('[data-action="model-new"]')).to_be_disabled()
                    page.unroute(setting_path)
                    expect(card('model')).to_have_attribute('data-state','configured', timeout=15000)
                    expect(page.locator('[data-action="model-new"]')).to_be_enabled()
                    # Failed health only: no blanket claim that model/switch/tasks failed.
                    page.route('**/api/health', lambda route: route.abort())
                    load()
                    expect(card('service')).to_have_attribute('data-state','unknown')
                    expect(card('model')).to_have_attribute('data-state','configured')
                    expect(card('analysis')).to_have_attribute('data-state','unavailable')
                    assert not errors, errors
                    assert not mutations, mutations
                    browser.close()
                assert snapshot() == before, 'settings observation changed one of the twenty business tables'
                print('PASS real state reads, independent off/stopped/unknown, partial poll/recovery, three widths; no writes/provider calls; twenty tables unchanged')
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            target_database.engine.dispose()


if __name__ == '__main__':
    run()
