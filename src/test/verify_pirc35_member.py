"""Current Review entry and original whole membership; fictional local app only."""
from datetime import datetime, timezone
import os
from pathlib import Path
import socket
import tempfile
import threading
import time

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright, Error as PlaywrightError
from serve_m2_ui import prepare_app
from browser_artifact import viewport_evidence


def publish(client, **intent):
    response = client.post('/paam/ledger/v1/review/preview',json=intent)
    assert response.status_code == 200, response.text
    plan = response.json()['body']
    assert not plan['blocking_issues'], plan
    response = client.post('/paam/ledger/v1/review/command',json=intent | dict(
        expected_reviews=plan['expected_reviews'],preview_digest=plan['preview_digest']))
    assert response.status_code == 200, response.text
    return response.json()['body']


def run():
    with tempfile.TemporaryDirectory(prefix='paam-pirc35-member-') as temporary:
        app = prepare_app(Path(temporary))
        from backend.core import target_database
        from backend.entity import TransactionFact, ReviewCase, LedgerEntry, ReviewAllocation
        from backend.mapper.review_command_mapper import ReviewCommandMapper
        from sqlalchemy import select, func, update
        ids = list(range(3,38))
        legacy_ids = list(range(100,221))
        with target_database.SessionLocal() as db:
            db.add_all([TransactionFact(id=i,fact_key=f'mock-members-{i}',cash_direction=2,amount=100,
                currency_code='CNY',account_code='',summary=f'Mock member {i}',
                occurred_time=datetime(2024,1,1,tzinfo=timezone.utc)) for i in ids + legacy_ids])
            db.flush()
            ReviewCommandMapper(db).create_initial_defaults(ids + legacy_ids)
            # Valid existing group above the expanded write budget: read must
            # remain complete, while a new 121-member publication is rejected.
            defaults = db.scalars(select(ReviewAllocation.review_id).where(ReviewAllocation.transaction_id.in_(legacy_ids))).all()
            db.execute(update(ReviewCase).where(ReviewCase.id.in_(defaults)).values(status=1))
            db.add(ReviewCase(id=500,behavior_type=4,status=0,title='Mock legacy 121 members'))
            for fid in legacy_ids:
                lid = 5000 + fid
                db.add(LedgerEntry(id=lid,entry_type=0,entry_direction=2,amount=100,currency_code='CNY',
                    account_ref_id=0,account_code='',occurred_time=datetime(2024,1,1,tzinfo=timezone.utc)))
                db.add(ReviewAllocation(id=lid,review_id=500,transaction_id=fid,ledger_id=lid,amount=100,currency_code='CNY'))
            db.commit()
        with target_database.SessionLocal() as db:
            db.add(TransactionFact(id=300,fact_key='mock-legacy-split',cash_direction=2,amount=100,
                currency_code='CNY',account_code='',summary='Mock legacy partial groups',
                occurred_time=datetime(2024,1,1,tzinfo=timezone.utc)))
            db.flush(); ReviewCommandMapper(db).create_initial_defaults([300]); db.flush()
            default_id = db.scalar(select(ReviewAllocation.review_id).where(ReviewAllocation.transaction_id==300))
            db.get(ReviewCase,default_id).status = 1
            for rid,amount in [(600,40),(601,60)]:
                db.add(ReviewCase(id=rid,behavior_type=4,status=0,title=f'Mock partial matter {rid}'))
                db.add(LedgerEntry(id=rid,entry_type=0,entry_direction=2,amount=amount,currency_code='CNY',
                    account_ref_id=0,account_code='',occurred_time=datetime(2024,1,1,tzinfo=timezone.utc)))
                db.add(ReviewAllocation(id=rid,review_id=rid,transaction_id=300,ledger_id=rid,amount=amount,currency_code='CNY'))
            db.commit()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        server = uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
        worker = threading.Thread(target=server.run,daemon=True); worker.start()
        try:
            with httpx.Client(base_url=base,trust_env=False) as client:
                for _ in range(100):
                    try:
                        if client.get('/api/health').status_code == 200: break
                    except httpx.HTTPError: pass
                    time.sleep(.1)
                else: raise RuntimeError('fictional app did not start')
                rid = publish(client,new_reviews=[dict(case_code='NORMAL',title='Mock original 35-member matter',
                    parameters=dict(transaction_ids=ids))])['created_reviews'][0]['id']
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel='msedge' if os.name=='nt' else None,headless=True)
                    page = browser.new_page(viewport={'width':1440,'height':900})
                    errors, previews, commands, reads = [], [], [], []
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    def record(request):
                        if request.method == 'POST' and request.url.endswith('/review/preview'): previews.append(request.post_data_json)
                        if request.method == 'POST' and request.url.endswith('/review/command'): commands.append(request.post_data_json)
                        if request.method == 'GET': reads.append(request.url)
                    page.on('request',record)
                    form = page.locator('[data-immutable-review]')
                    dialog = page.locator('dialog[open] [data-review-members]')
                    def open_current():
                        form.locator('[data-current-review-selection] button').click()
                        expect(dialog.locator('[data-member-count]')).to_contain_text('原成员共')
                    page.goto(base + '/#workbench/review?facts=3')
                    expect(form.locator('[data-cash-row]')).to_have_count(1)
                    expect(form.locator('[data-current-review-selection]')).to_contain_text('Mock original 35-member matter')
                    expect(form.locator('[data-current-review-selection]')).to_contain_text('35 个完整事实')
                    form.locator('[name="title"]').fill('Mock retained replacement')
                    first_key = form.locator('[data-cash-row]').get_attribute('data-draft-id')
                    open_current()
                    expect(dialog.locator('[data-member-items] article')).to_have_count(20)
                    assert dialog.locator('[data-member-items] article').first.bounding_box()['height'] <= 80
                    # Reuse original Review detail read-only, including its
                    # existing paged fallback. Never add another history UI.
                    detail_url = f'**/paam/ledger/v1/review/{rid}'
                    page.route(detail_url,lambda route:route.fulfill(status=413,json={'status':413,'message':'Mock original detail exceeds capacity','body':{'code':'DETAIL_LIMIT'}}))
                    dialog.locator('[data-member-original]').click()
                    inspection = page.locator('dialog[open].inspection-workspace')
                    expect(inspection.locator('[data-review-relation]')).to_have_count(5)
                    expect(inspection.locator('[data-inspect-actions]')).to_contain_text('只读')
                    expect(inspection.locator('[data-inspect-actions] button')).to_have_count(0)
                    expect(inspection.locator('[data-review-relation="flow"] [data-rel-items] article')).to_have_count(20)
                    inspection.locator('[data-close]').click()
                    page.unroute(detail_url)
                    expect(dialog).to_have_count(1)
                    dialog.locator('[data-member-next]').click()
                    expect(dialog.locator('[data-member-items] article')).to_have_count(15)
                    expect(dialog.locator('[data-member-items]')).to_contain_text('Mock member 37')
                    viewport_evidence(page,'fix-r08-original-members-page2')
                    page.locator('dialog[open] [data-workbench-close]').click()
                    expect(form.locator('[data-cash-row]')).to_have_count(1)
                    expect(form.locator('[name="title"]')).to_have_value('Mock retained replacement')
                    assert not previews and not commands
                    # The group changes while its dialog is open. Final-state
                    # read must reject, not silently extend the draft.
                    open_current()
                    publish(client,deactivate_review_ids=[rid])
                    dialog.locator('[data-select-review-group]').click()
                    expect(dialog.locator('[data-member-status]')).to_contain_text('状态已变化')
                    expect(form.locator('[data-cash-row]')).to_have_count(1)
                    dialog.locator('[data-member-refresh]').click()
                    expect(dialog.locator('[data-member-header]')).to_contain_text('已停用')
                    expect(dialog.locator('[data-select-review-group]')).to_be_disabled()
                    expect(dialog.locator('[data-member-items]')).to_contain_text('当前归属')
                    page.locator('dialog[open] [data-workbench-close]').click()
                    publish(client,activate_review_ids=[rid])
                    page.reload()
                    expect(form.locator('[data-cash-row]')).to_have_count(1)
                    form.locator('[name="title"]').fill('Mock retained replacement')
                    first_key = form.locator('[data-cash-row]').get_attribute('data-draft-id')
                    open_current()
                    start = len(reads)
                    dialog.locator('[data-select-review-group]').click()
                    expect(dialog).to_have_count(0)
                    expect(form.locator('[data-cash-row]')).to_have_count(35)
                    assert form.locator('[data-cash-row]').first.get_attribute('data-draft-id') == first_key
                    expect(form.locator('[name="title"]')).to_have_value('Mock retained replacement')
                    assert not any('/candidate/' in url for url in reads[start:]), reads[start:]
                    assert not previews and not commands
                    form.locator('[data-review-preview]').click()
                    expect(form.locator('[data-review-command]')).to_be_enabled()
                    assert {row['transaction_id'] for row in previews[-1]['new_reviews'][0]['parameters']['allocations']} == set(ids)
                    expect(form.locator('[data-review-impact]')).to_contain_text(f'#{rid}')
                    assert not commands
                    form.locator('[data-review-command]').click()
                    expect(form).to_have_count(0)
                    assert len(commands) == 1
                    # A failure on page 2 of a complete 121-member read cannot
                    # apply the successfully read first 100 to the draft.
                    page.goto(base + '/#workbench/review?facts=100')
                    expect(form.locator('[data-cash-row]')).to_have_count(1)
                    failed_url = '**/review/500/fact/list?page_index=2&page_size=100'
                    page.route(failed_url,lambda route:route.fulfill(status=503,json={'status':503,'message':'Mock member page read failed','body':{'code':'QUERY_BUSY'}}))
                    open_current()
                    dialog.locator('[data-select-review-group]').click()
                    expect(dialog.locator('[data-member-status]')).to_contain_text('Mock member page read failed')
                    expect(form.locator('[data-cash-row]')).to_have_count(1)
                    page.unroute(failed_url)
                    # Cancel while the second page is pending. A late response
                    # must not change the parent draft after the dialog closes.
                    held = []
                    def hold(route): held.append((route,route.fetch()))
                    page.route(failed_url,hold)
                    dialog.locator('[data-select-review-group]').click()
                    expect(dialog.locator('[data-member-status]')).to_contain_text('尚未改变选择')
                    for _ in range(100):
                        if held: break
                        page.wait_for_timeout(20)
                    assert held
                    page.locator('dialog[open] [data-workbench-close]').click()
                    try: held[0][0].fulfill(response=held[0][1])
                    except PlaywrightError: pass  # The browser may already abort it.
                    page.unroute(failed_url)
                    page.wait_for_timeout(100)
                    expect(form.locator('[data-cash-row]')).to_have_count(1)
                    open_current()
                    dialog.locator('[data-select-review-group]').click()
                    expect(dialog).to_have_count(0)
                    expect(form.locator('[data-cash-row]')).to_have_count(121)
                    form.locator('[data-review-preview]').click()
                    expect(form.locator('[data-review-impact]')).to_contain_text('REVIEW_CHANGE_LIMIT')
                    expect(form.locator('[data-review-command]')).to_be_disabled()
                    assert len(commands) == 1
                    # Current group inspection is also available on unselected
                    # candidates, not only after preselection or through IDs.
                    page.goto(base + '/#workbench/review')
                    expect(form.locator('[data-picker-id]')).to_have_count(20)
                    candidate = form.locator('.picker-list-row',has_text='Mock member 3 /')
                    expect(candidate).to_contain_text('当前事项')
                    candidate.locator('[data-picker-action]').click()
                    expect(dialog.locator('[data-member-count]')).to_contain_text('原成员共 35 项')
                    page.set_viewport_size({'width':390,'height':844})
                    viewport_evidence(page,'fix-r08-current-members-narrow')
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    dialog.locator('[data-member-items] article').last.scroll_into_view_if_needed()
                    tools = dialog.locator('.review-member-tools')
                    expect(tools).to_be_visible()
                    assert tools.bounding_box()['height'] <= 160
                    assert tools.evaluate("node => getComputedStyle(node).backgroundColor") != 'rgba(0, 0, 0, 0)'
                    assert dialog.locator('[data-member-items] article').first.bounding_box()['height'] <= 110
                    page.locator('dialog[open] [data-workbench-close]').click()
                    expect(form.locator('[data-cash-row]')).to_have_count(0)
                    # Legacy partial groups must not be reduced to an arbitrary
                    # one. The chooser displays both before opening one group.
                    page.set_viewport_size({'width':1440,'height':900})
                    form.locator('[data-picker-word]').fill('Mock legacy partial groups')
                    form.locator('[data-picker-search]').click()
                    expect(form.locator('[data-picker-id]')).to_have_count(1)
                    expect(form.locator('.picker-list-row')).to_contain_text('当前有效事项 2 个')
                    form.locator('[data-picker-action]').click()
                    groups = page.locator('dialog[open] [data-current-groups]')
                    expect(groups.locator('button')).to_have_count(2)
                    groups.get_by_role('button',name='Mock partial matter 601',exact=False).click()
                    expect(dialog.locator('[data-member-count]')).to_contain_text('原成员共 1 项')
                    expect(dialog.locator('[data-member-items]')).to_contain_text('Mock partial matter 600')
                    expect(dialog.locator('[data-member-items]')).to_contain_text('Mock partial matter 601')
                    page.locator('dialog[open] [data-workbench-close]').click()
                    expect(form.locator('[data-cash-row]')).to_have_count(0)
                    assert not errors, errors
                    browser.close()
                with target_database.SessionLocal() as db:
                    assert db.get(ReviewCase,rid).status == 1
                    assert db.scalar(select(func.count()).select_from(ReviewAllocation).where(ReviewAllocation.review_id==500)) == 121
                    assert db.get(ReviewCase,500).status == 0
                    replacement = db.scalar(select(ReviewCase.id).where(ReviewCase.title=='Mock retained replacement'))
                    assert replacement and db.get(ReviewCase,replacement).status == 0
                    assert db.scalar(select(func.count()).select_from(ReviewAllocation).where(ReviewAllocation.review_id==replacement)) == 35
            print('PASS current Review entry: complete original members, explicit whole choice, stale/read-failure/cancel safety and one real publication')
        finally:
            server.should_exit = True; worker.join(timeout=10); target_database.engine.dispose()


if __name__ == '__main__': run()
