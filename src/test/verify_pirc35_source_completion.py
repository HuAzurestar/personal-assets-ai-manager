"""Actual ordinary historical source bulk, immutable full groups, fresh SQLite."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import sqlite3
import tempfile
import threading
import time

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright
from serve_m2_ui import prepare_app
from browser_artifact import viewport_evidence


def run():
    with tempfile.TemporaryDirectory(prefix='paam-source-completion-') as temporary:
        directory=Path(temporary);app=prepare_app(directory)
        from backend.core import target_database
        from backend.entity import TransactionFact, LedgerAccountParty, LedgerAccountRef
        from backend.mapper.review_command_mapper import ReviewCommandMapper
        from sqlalchemy import select
        try:
            with target_database.SessionLocal() as db:
                db.add(LedgerAccountParty(id=1,name='Mock bulk owner',status='ACTIVE'))
                db.add_all([LedgerAccountRef(id=i,account_id=0,name=f'Mock bulk source {i}',status='ACTIVE') for i in (1,2)])
                specs=[(10,2,40000,'CNY'),(11,1,20000,'CNY'),(12,2,1000,'KRW')]+[(i,2,10000,'CNY_4') for i in range(20,124)]
                db.add_all([TransactionFact(id=i,fact_key=f'Mock bulk {i}',account_code='',
                    occurred_time=datetime(2032,1,1,tzinfo=timezone.utc),cash_direction=direction,amount=amount,
                    currency_code=unit,summary=f'Mock bulk cash {i}') for i,direction,amount,unit in specs])
                db.flush();ReviewCommandMapper(db).create_initial_defaults([row[0] for row in specs]);db.commit()
        except Exception:
            target_database.engine.dispose();raise
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
        worker=threading.Thread(target=server.run,daemon=True);worker.start()
        try:
            with httpx.Client(base_url=base,trust_env=False,timeout=30) as client:
                for _ in range(100):
                    try:
                        if client.get('/api/health').status_code==200:break
                    except httpx.HTTPError:pass
                    time.sleep(.1)
                else:raise RuntimeError('fictional app did not start')
                def get(path):
                    response=client.get(path);assert response.status_code==200,response.text
                    return response.json()['body']
                def execute(intent):
                    response=client.post('/paam/ledger/v1/review/preview',json=intent);assert response.status_code==200,response.text
                    plan=response.json()['body'];assert not plan['blocking_issues'],plan
                    response=client.post('/paam/ledger/v1/review/command',json=intent|dict(expected_reviews=plan['expected_reviews'],preview_digest=plan['preview_digest']))
                    assert response.status_code==200,response.text
                    return response.json()['body']
                def snapshot():
                    with target_database.SessionLocal() as db:
                        return {table.name:tuple(tuple(row) for row in db.execute(select(table).order_by(table.c.id)))
                            for table in target_database.TargetBase.metadata.sorted_tables}
                def signature(row):
                    return ([(allocation['transaction_id'],flow['economic_type'],flow['cash_amount'],flow['cash_currency_code'],flow['cash_direction'],flow['occurred_time'],flow['account_ref_id'])
                        for allocation in row['allocations'] for flow in row['ledger_entries'] if flow['id']==allocation['ledger_id']],
                        [(leg['position_id'],leg['type'],leg['leg_amount'],leg['leg_direction'],leg['occurred_time'],leg['basis']) for leg in row['position_legs']],
                        [(link['cash_amount'],link['cash_currency_code']) for link in row['position_allocations']])

                opening=execute(dict(new_reviews=[dict(case_code='BORROW_REPAY',title='Mock bulk split',parameters=dict(
                    new_positions=[dict(title='Mock bulk claim',type='ASSET',usage_scenario='PERSONAL-LENDING',party_id=1,unit_code='CNY')],
                    allocations=[dict(transaction_id=10,economic_type='ASSET_LIABILITY',cash_amount=30000,account_ref_id=0),
                        dict(transaction_id=10,economic_type='TRANSACTION',cash_amount=10000,account_ref_id=1),
                        dict(transaction_id=12,economic_type='TRANSACTION',cash_amount=1000,account_ref_id=0)],
                    legs=[dict(new_position_index=0,type='MOVEMENT',leg_direction='IN',leg_amount=30000,source=0,
                        occurred_time='2032-01-01T00:00:00Z',basis='Mock bulk opening')],
                    position_allocations=[dict(allocation_index=0,leg_index=0,cash_amount=30000,cash_currency_code='CNY')]))]))
                rid=opening['created_reviews'][0]['id'];pid=opening['created_positions'][0]['id'];old=get(f'/paam/ledger/v1/review/{rid}')
                source=old['position_legs'][0]['id']
                repayment=execute(dict(new_reviews=[dict(case_code='BORROW_REPAY',title='Mock bulk repayment',parameters=dict(
                    allocations=[dict(transaction_id=11,economic_type='ASSET_LIABILITY',cash_amount=20000,account_ref_id=0)],
                    legs=[dict(existing_position_id=pid,type='MOVEMENT',leg_direction='OUT',leg_amount=20000,source=source,
                        occurred_time='2032-01-01T00:00:00Z',basis='Mock bulk repayment')],
                    position_allocations=[dict(allocation_index=0,leg_index=0,cash_amount=20000,cash_currency_code='CNY')]))]))['created_reviews'][0]['id']
                old_out=get(f'/paam/ledger/v1/review/{repayment}')
                # The common browser helper intentionally seeds two additional
                # CNY flows with tags. They belong to the visible all/CNY scope;
                # preserve and verify them, rather than silently excluding them.
                seeded=[get(f'/paam/ledger/v1/review/{get(f"/paam/ledger/v1/flow/{id}")["reviews"][0]["id"]}') for id in (1,2)]
                assert len(seeded)==2 and all(row['ledger_entries'][0]['account_ref_id']==0 for row in seeded)
                before=snapshot()
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(channel='msedge' if os.name=='nt' else None,headless=True)
                    page=browser.new_page(viewport={'width':1280,'height':800});errors=[];commands=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.on('request',lambda req:commands.append(req.post_data_json)
                        if req.method=='POST' and req.url.endswith('/paam/ledger/v1/review/command') else None)
                    page.goto(base+'/#workbench/account')
                    page.locator('[data-account-complete-source]').click()
                    form=page.locator('[data-source-completion-form]');expect(form).to_be_visible()
                    expect(form.locator('[data-source-count]')).to_contain_text('当前已应用筛选 109 条')
                    assert form.locator('[name="cash_amount"], [name="case_code"], [name="leg_amount"]').count()==0
                    form.locator('[data-source-select-page]').click()
                    expect(form.locator('[data-source-count]')).to_contain_text('已选 20 条')
                    form.locator('[data-source-next]').click()
                    expect(form.locator('[data-source-page]')).to_contain_text('第 2 /')
                    form.locator('[data-source-select-page]').click()
                    expect(form.locator('[data-source-count]')).to_contain_text('已选 40 条')
                    form.locator('[data-source-clear]').click()
                    form.locator('[data-source-select-all]').click()
                    expect(form.locator('[data-source-read-status]')).to_contain_text('超过每次100条')
                    expect(form.locator('[data-source-count]')).to_contain_text('已选 0 条')
                    assert not commands and snapshot()==before
                    form.locator('[name="currency"]').select_option('CNY')
                    form.locator('[data-source-search]').click()
                    expect(form.locator('[data-source-count]')).to_contain_text('当前已应用筛选 4 条')
                    form.locator('[data-source-select-all]').click()
                    expect(form.locator('[data-source-count]')).to_contain_text('已选 4 条')
                    def choose(ref):
                        form.locator('[data-named-choice="account_ref_id"] [data-choice-pick]').click()
                        page.locator(f'dialog[open] [data-picker-id="{ref}"]').click()
                        expect(form.locator('[name="account_ref_id"]')).to_have_value(str(ref))
                    choose(2)
                    def fail_original(route):
                        route.fulfill(status=503,content_type='application/json',body=json.dumps(dict(status=503,message='Mock full original unavailable',body=dict(code='QUERY_BUSY'))))
                    original_url=f'**/paam/ledger/v1/review/{rid}'
                    page.route(original_url,fail_original)
                    form.locator('[data-review-preview]').click()
                    expect(form.locator('[data-review-status]')).to_contain_text('QUERY_BUSY')
                    expect(form.locator('[data-review-command]')).to_be_disabled()
                    assert not commands and snapshot()==before
                    page.unroute(original_url,fail_original)
                    form.locator('[data-review-preview]').click();expect(form.locator('[data-review-command]')).to_be_enabled()
                    business=form.locator('[data-correction-business]');expect(business.locator('[data-correction-cash]')).to_have_count(6)
                    expect(business).to_contain_text('100.00 CNY → 100.00 CNY')
                    expect(business).to_contain_text('KRW');expect(business).to_contain_text('Mock bulk source 2')
                    for width in (1280,820,390):
                        page.set_viewport_size({'width':width,'height':800});form.locator('[data-source-count]').scroll_into_view_if_needed()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width
                        viewport_evidence(page,f'dev17-source-completion-list-{width}')
                        business.scroll_into_view_if_needed();assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width
                        viewport_evidence(page,f'dev17-source-completion-preview-{width}')
                    page.set_viewport_size({'width':1280,'height':800});assert snapshot()==before
                    lock=sqlite3.connect(directory/'m2-ui.db')
                    try:
                        lock.execute('BEGIN IMMEDIATE')
                        with page.expect_response('**/paam/ledger/v1/review/command') as response:form.locator('[data-review-command]').click()
                        assert response.value.status==503 and response.value.json()['body']['code']=='WRITE_BUSY'
                    finally:lock.rollback();lock.close()
                    expect(form.locator('[data-review-status]')).to_contain_text('本次未提交')
                    assert snapshot()==before and len(commands)==1
                    form.locator('[data-review-preview]').click();expect(form.locator('[data-review-command]')).to_be_enabled()
                    with page.expect_response('**/paam/ledger/v1/review/command') as response:form.locator('[data-review-command]').click()
                    result=response.value.json()['body'];assert len(result['created_reviews'])==4
                    expect(form.locator('[data-review-status]')).to_contain_text('所选 4 条来源补齐已发布')
                    copied=[get(f'/paam/ledger/v1/review/{row["id"]}') for row in result['created_reviews']]
                    new=next(row for row in copied if row['title']==old['title']);new_out=next(row for row in copied if row['title']==old_out['title'])
                    for original,current in ((old,new),(old_out,new_out)):
                        expected=signature(original)
                        expected[0][:]=[cash[:6]+(2 if cash[0] in (10,11) and cash[1]=='ASSET_LIABILITY' else cash[6],) for cash in expected[0]]
                        assert signature(current)==expected
                        preserved=get(f'/paam/ledger/v1/review/{original["id"]}');assert preserved['status']=='REVOKED'
                        for key in ('allocations','ledger_entries','position_legs','position_allocations','positions'):assert preserved[key]==original[key]
                    assert new_out['position_legs'][0]['source_position_leg_id']==new['position_legs'][0]['id']
                    for original in seeded:
                        current=next(row for row in copied if row['allocations'][0]['transaction_id']==original['allocations'][0]['transaction_id'])
                        expected=signature(original);expected[0][:]=[cash[:6]+(2,) for cash in expected[0]]
                        assert signature(current)==expected
                        preserved=get(f'/paam/ledger/v1/review/{original["id"]}');assert preserved['status']=='REVOKED'
                        for key in ('allocations','ledger_entries','position_legs','position_allocations','positions'):assert preserved[key]==original[key]
                    form.locator('[data-source-current]').click()
                    expect(form.locator('[data-source-current-result]')).to_contain_text('完整只读核对 4 条')
                    expect(form.locator('[data-source-current-result]')).to_contain_text('已停用，内容保留')

                    # Remaining KRW source completes in another explicitly chosen
                    # scope. The real commit succeeds but its reply is unknown.
                    page.goto(base+'/#workbench/account');page.locator('[data-account-complete-source]').click()
                    expect(form).to_be_visible();expect(form.locator('[data-source-count]')).to_contain_text('当前已应用筛选 105 条')
                    form.locator('[name="currency"]').select_option('KRW');form.locator('[data-source-search]').click()
                    expect(form.locator('[data-source-count]')).to_contain_text('当前已应用筛选 1 条')
                    form.locator('[data-source-select-all]').click();expect(form.locator('[data-source-count]')).to_contain_text('已选 1 条');choose(2)
                    form.locator('[data-review-preview]').click();expect(form.locator('[data-review-command]')).to_be_enabled()
                    def lose_response(route):
                        response=route.fetch();assert response.ok,response.text()
                        route.fulfill(status=503,content_type='application/json',body=json.dumps(dict(status=503,message='Mock lost committed reply',body=dict(code='RESULT_UNKNOWN'))))
                    page.route('**/paam/ledger/v1/review/command',lose_response)
                    form.locator('[data-review-command]').click();expect(form.locator('[data-review-status]')).to_contain_text('提交结果未知')
                    expect(form.locator('[data-review-preview]')).to_be_disabled();expect(form.locator('[data-review-command]')).to_be_disabled()
                    expect(form.locator('[data-source-select-all]')).to_be_disabled()
                    committed=snapshot();count=len(commands)
                    form.locator('[data-source-current]').click();expect(form.locator('[data-source-current-result]')).to_contain_text('已停用，内容保留')
                    expect(form.locator('[data-source-current-result]')).to_contain_text('Mock bulk source 2')
                    form.evaluate('node=>node.requestSubmit()');form.locator('[data-review-preview]').evaluate('node=>node.click()')
                    form.locator('[data-source-select-all]').evaluate('node=>node.click()')
                    assert len(commands)==count==3 and snapshot()==committed
                    assert all(set(cmd)=={'account_corrections','correction_duplicates','expected_reviews','preview_digest'} for cmd in commands)
                    assert len(commands[1]['account_corrections'])==4 and len(commands[2]['account_corrections'])==1
                    assert before['transaction_fact']==committed['transaction_fact']
                    assert before['ledger_account_ref']==committed['ledger_account_ref']
                    assert before['transaction_import_row']==committed['transaction_import_row']
                    assert not errors,errors
                    browser.close()
                print('PASS ordinary source bulk across pages, 100 budget without partial scope, full split/quantity preservation, real busy rollback and committed unknown without replay')
        finally:
            server.should_exit=True;worker.join(timeout=10);target_database.engine.dispose()


if __name__=='__main__':run()
