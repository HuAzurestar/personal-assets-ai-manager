"""Actual account-only correction, immutable graph, failure and unknown guards."""
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
    with tempfile.TemporaryDirectory(prefix='paam-account-only-') as temporary:
        directory=Path(temporary);app=prepare_app(directory)
        from backend.core import target_database
        from backend.entity import TransactionFact, LedgerAccountParty, LedgerAccountRef
        from backend.entity.base import Base
        from backend.mapper.review_command_mapper import ReviewCommandMapper
        from sqlalchemy import select
        try:
            with target_database.SessionLocal() as db:
                db.add(LedgerAccountParty(id=1,name='Mock correction owner',status='ACTIVE'))
                db.add_all([LedgerAccountRef(id=i,account_id=0,name=f'Mock correction source {i}',status='ACTIVE') for i in (1,2)])
                db.add_all([TransactionFact(id=i,fact_key=f'Mock correction {i}',account_code='',
                    occurred_time=datetime(2032,1,1,tzinfo=timezone.utc),cash_direction=direction,amount=amount,
                    currency_code=unit,summary=f'Mock correction cash {i}')
                    for i,direction,amount,unit in ((10,2,40000,'CNY'),(11,1,20000,'CNY'),(12,2,1000,'KRW'))])
                db.flush();ReviewCommandMapper(db).create_initial_defaults([10,11,12]);db.commit()
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
                    response=client.post('/paam/ledger/v1/review/preview',json=intent)
                    assert response.status_code==200,response.text
                    plan=response.json()['body'];assert not plan['blocking_issues'],plan
                    response=client.post('/paam/ledger/v1/review/command',json=intent|dict(
                        expected_reviews=plan['expected_reviews'],preview_digest=plan['preview_digest']))
                    assert response.status_code==200,response.text
                    return response.json()['body']

                def snapshot():
                    with target_database.SessionLocal() as db:
                        return {table.name:tuple(tuple(row) for row in db.execute(select(table).order_by(table.c.id)))
                            for table in Base.metadata.sorted_tables}

                def signature(row):
                    return ([(allocation['transaction_id'],flow['economic_type'],flow['cash_amount'],flow['cash_currency_code'],
                        flow['cash_direction'],flow['occurred_time'],flow['account_ref_id'])
                        for allocation in row['allocations'] for flow in row['ledger_entries'] if flow['id']==allocation['ledger_id']],
                        [(leg['position_id'],leg['type'],leg['leg_amount'],leg['leg_direction'],leg['occurred_time'],leg['basis']) for leg in row['position_legs']],
                        [(link['cash_amount'],link['cash_currency_code']) for link in row['position_allocations']])

                opening=execute(dict(new_reviews=[dict(case_code='BORROW_REPAY',title='Mock split borrowing',parameters=dict(
                    new_positions=[dict(title='Mock correction claim',type='ASSET',usage_scenario='PERSONAL-LENDING',party_id=1,unit_code='CNY')],
                    allocations=[dict(transaction_id=10,economic_type='ASSET_LIABILITY',cash_amount=30000,account_ref_id=1),
                        dict(transaction_id=10,economic_type='TRANSACTION',cash_amount=10000,account_ref_id=1),
                        dict(transaction_id=12,economic_type='TRANSACTION',cash_amount=1000,account_ref_id=1)],
                    legs=[dict(new_position_index=0,type='MOVEMENT',leg_direction='IN',leg_amount=30000,source=0,
                        occurred_time='2032-01-01T00:00:00Z',basis='Mock opening')],
                    position_allocations=[dict(allocation_index=0,leg_index=0,cash_amount=30000,cash_currency_code='CNY')]))]))
                rid=opening['created_reviews'][0]['id'];pid=opening['created_positions'][0]['id']
                old=get(f'/paam/ledger/v1/review/{rid}');lid=old['ledger_entries'][0]['id'];source=old['position_legs'][0]['id']
                repayment=execute(dict(new_reviews=[dict(case_code='BORROW_REPAY',title='Mock repayment',parameters=dict(
                    allocations=[dict(transaction_id=11,economic_type='ASSET_LIABILITY',cash_amount=20000,account_ref_id=1)],
                    legs=[dict(existing_position_id=pid,type='MOVEMENT',leg_direction='OUT',leg_amount=20000,source=source,
                        occurred_time='2032-01-01T00:00:00Z',basis='Mock repayment')],
                    position_allocations=[dict(allocation_index=0,leg_index=0,cash_amount=20000,cash_currency_code='CNY')]))]))['created_reviews'][0]['id']
                old_out=get(f'/paam/ledger/v1/review/{repayment}');before=snapshot()

                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(channel='msedge' if os.name=='nt' else None,headless=True)
                    page=browser.new_page(viewport={'width':1280,'height':800});errors=[];commands=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.on('request',lambda request:commands.append(request.post_data_json)
                        if request.method=='POST' and request.url.endswith('/paam/ledger/v1/review/command') else None)
                    page.goto(base+'/#details/ledger?sort_field=id&sort_order=desc&page_size=20')
                    page.locator(f'[data-economic-row="{lid}"] [data-action="economic-detail"]').click()
                    page.locator('.inspection-workspace[open] [data-action="edit-ledger-account"]').click()
                    form=page.locator('[data-account-only]');expect(form).to_be_visible()
                    assert 'correct_ledger=' in page.url and 'facts=' not in page.url
                    assert form.locator('[name="cash_amount"], [name="case_code"], [name="leg_amount"]').count()==0

                    def choose(ref):
                        form.locator('[data-named-choice="account_ref_id"] [data-choice-pick]').click()
                        page.locator(f'dialog[open] [data-choice-picker] [data-picker-id="{ref}"]').click()
                        expect(form.locator('[name="account_ref_id"]')).to_have_value(str(ref))

                    choose(2)
                    # A successful financial preview is insufficient if the complete
                    # original cannot be read for business review. No command enabled.
                    def fail_original(route):
                        route.fulfill(status=503,content_type='application/json',body=json.dumps(dict(
                            status=503,message='Mock original read failed',body=dict(code='QUERY_BUSY'))))
                    original_url=f'**/paam/ledger/v1/review/{rid}'
                    page.route(original_url,fail_original)
                    form.locator('[data-review-preview]').click()
                    expect(form.locator('[data-review-status]')).to_contain_text('QUERY_BUSY')
                    expect(form.locator('[data-review-command]')).to_be_disabled()
                    assert not commands and snapshot()==before
                    page.unroute(original_url,fail_original)
                    form.locator('[data-review-preview]').click()
                    expect(form.locator('[data-review-command]')).to_be_enabled()
                    business=form.locator('[data-correction-business]')
                    expect(business.locator('[data-correction-cash]')).to_have_count(4)
                    expect(business).to_contain_text('100.00 CNY → 100.00 CNY')
                    expect(business).to_contain_text('KRW');expect(business).to_contain_text('Mock correction source 2')
                    for width in (1280,820,390):
                        page.set_viewport_size({'width':width,'height':800});business.scroll_into_view_if_needed()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width
                        viewport_evidence(page,f'dev17-account-correction-{width}')
                    page.set_viewport_size({'width':1280,'height':800})
                    assert snapshot()==before
                    lock=sqlite3.connect(directory/'m2-ui.db')
                    try:
                        lock.execute('BEGIN IMMEDIATE')
                        with page.expect_response('**/paam/ledger/v1/review/command') as response:
                            form.locator('[data-review-command]').click()
                        assert response.value.status==503 and response.value.json()['body']['code']=='WRITE_BUSY'
                    finally:lock.rollback();lock.close()
                    expect(form.locator('[data-review-status]')).to_contain_text('本次未提交')
                    expect(form.locator('[data-review-command]')).to_be_disabled()
                    expect(form.locator('[name="account_ref_id"]')).to_have_value('2')
                    assert snapshot()==before and len(commands)==1
                    form.locator('[data-review-preview]').click();expect(form.locator('[data-review-command]')).to_be_enabled()
                    with page.expect_response('**/paam/ledger/v1/review/command') as response:
                        form.locator('[data-review-command]').click()
                    result=response.value.json()['body'];assert len(result['created_reviews'])==2
                    expect(form.locator('[data-review-status]')).to_contain_text('来源更正已发布')
                    expect(form.locator('[data-review-command]')).to_be_disabled()
                    copied=[get(f'/paam/ledger/v1/review/{row["id"]}') for row in result['created_reviews']]
                    new=next(row for row in copied if row['title']==old['title'])
                    expected=signature(old);expected[0][0]=expected[0][0][:-1]+(2,)
                    assert signature(new)==expected
                    new_out=next(row for row in copied if row['title']==old_out['title'])
                    assert signature(new_out)==signature(old_out)
                    assert new_out['position_legs'][0]['source_position_leg_id']==new['position_legs'][0]['id']
                    for original_row in (old,old_out):
                        preserved=get(f'/paam/ledger/v1/review/{original_row["id"]}')
                        assert preserved['status']=='REVOKED'
                        for key in ('allocations','ledger_entries','position_legs','position_allocations','positions'):
                            assert preserved[key]==original_row[key]
                    form.locator('[data-correction-current]').click()
                    expect(form.locator('[data-correction-current-result]')).to_contain_text('已停用，内容保留')

                    # Real second publication succeeds, then its response is lost.
                    # Querying the current state never permits another old command.
                    fee=new['ledger_entries'][1]['id']
                    page.goto(base+f'/#workbench/reviews?correct_ledger={fee}')
                    expect(form).to_be_visible();choose(2)
                    form.locator('[data-review-preview]').click();expect(form.locator('[data-review-command]')).to_be_enabled()
                    def lose_response(route):
                        response=route.fetch();assert response.ok,response.text()
                        route.fulfill(status=503,content_type='application/json',body=json.dumps(dict(
                            status=503,message='Mock lost committed response',body=dict(code='RESULT_UNKNOWN'))))
                    page.route('**/paam/ledger/v1/review/command',lose_response)
                    form.locator('[data-review-command]').click()
                    expect(form.locator('[data-review-status]')).to_contain_text('提交结果未知')
                    expect(form.locator('[data-review-preview]')).to_be_disabled()
                    expect(form.locator('[data-review-command]')).to_be_disabled()
                    expect(form.locator('[data-named-choice="account_ref_id"] [data-choice-pick]')).to_be_disabled()
                    assert get(f'/paam/ledger/v1/flow/{fee}')['active'] is False
                    count=len(commands);committed=snapshot()
                    form.locator('[data-correction-current]').click()
                    expect(form.locator('[data-correction-current-result]')).to_contain_text('已停用，内容保留')
                    expect(form.locator('[data-correction-current-result]')).to_contain_text('Mock correction source 2')
                    form.evaluate('node => node.requestSubmit()')
                    form.locator('[data-review-preview]').evaluate('node => node.click()')
                    assert len(commands)==count==3 and snapshot()==committed
                    assert all(set(command)=={'account_corrections','correction_duplicates','expected_reviews','preview_digest'} for command in commands)
                    assert not errors,errors
                    viewport_evidence(page,'dev17-account-correction-unknown')
                    browser.close()
                print('PASS actual account-only whole split/quantity closure, complete preview read failure, real busy rollback and committed unknown query without replay')
        finally:
            server.should_exit=True;worker.join(timeout=10);target_database.engine.dispose()


if __name__=='__main__':run()
