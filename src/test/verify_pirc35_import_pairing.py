"""Complete named bulk pair UI, manual ambiguity and real LINK/DUP writes; fiction only."""
import base64
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace

import httpx
import uvicorn
from playwright.sync_api import Error as BrowserError, expect, sync_playwright

from serve_m2_ui import prepare_app
from browser_artifact import viewport_evidence


def statement(account, amounts, prefix):
    return ('建设银行个人交易明细\n' + f'账号：{account}\n姓名：Mock测试用户\n币种：人民币\n'
        '摘要,币别,交易日期,交易金额,账户余额,交易地点/附言,对方账号与户名\n'
        + ''.join(f'{prefix}{i},CNY,2024-03-15,-{amount}.00,10000.00,Mock用途,Mock商户\n'
            for i,amount in enumerate(amounts,1))).encode()


def run():
    with tempfile.TemporaryDirectory(prefix='paam-import-pairing-') as temporary:
        app=prepare_app(Path(temporary))
        from backend.core import target_database
        from backend.service.configured_llm_analyzer import ConfiguredLlmAnalyzer
        from test_pirc35_import_duplicate import manifest
        from import_batch_helpers import confirm_api_batch
        provider_calls=[]

        async def forbid_provider(*_args,**_kwargs):
            provider_calls.append(True)
            raise AssertionError('Fictional pairing must not call a model provider')

        ConfiguredLlmAnalyzer.analyze=forbid_provider

        def snapshot():
            with target_database.SessionLocal() as db:
                return manifest(SimpleNamespace(db=db))

        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
        worker=threading.Thread(target=server.run,daemon=True);worker.start()
        try:
            with httpx.Client(base_url=base,trust_env=False,timeout=35) as client:
                for _ in range(100):
                    try:
                        if client.get('/api/health').status_code==200:break
                    except httpx.HTTPError:pass
                    time.sleep(.1)
                else:raise RuntimeError('fictional app did not start')
                origin=client.post('/paam/import/v1/preview',json={'files':[dict(filename='Mock-original.csv',source_type='ccb',
                    content_base64=base64.b64encode(statement('990000000000001234',list(range(1,31))+[999,999],'Mock原事实')).decode())]})
                assert origin.status_code==200,origin.text
                seeded=confirm_api_batch(client,origin.json()['body'],explicit_new=True)
                assert seeded.status_code==200,seeded.text
                seed_ids={row['transaction_id'] for row in seeded.json()['body']['processed_rows']}
                assert len(seed_ids)==32
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(channel='msedge' if os.name=='nt' else None,headless=True)
                    page=browser.new_page(viewport={'width':1280,'height':900})
                    errors,puts,writes,pairs,responses=[],[],[],[],[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.on('request',lambda request:puts.append(request.post_data_json)
                        if request.method=='PUT' and '/paam/import/v1/preview/' in request.url else None)
                    page.on('request',lambda request:writes.append(request.post_data_json)
                        if request.method=='POST' and request.url.endswith('/confirm') else None)
                    page.on('request',lambda request:pairs.append(request.post_data_json)
                        if request.method=='POST' and request.url.endswith('/pairing-preview') else None)

                    def confirm_visible_batch():
                        # The real server commits, but the browser loses this
                        # financial response. Do not simulate acceptance in JS.
                        def lost_response(route):
                            response=route.fetch()
                            assert response.status==200,(response.status,response.text())
                            responses.append(response.json()['body'])
                            route.abort('failed')
                        confirm_pattern='**/paam/import/v1/preview/*/confirm'
                        page.route(confirm_pattern,lost_response)
                        page.locator('[data-batch-confirm]').click()
                        expect(page.locator('[data-batch-status]')).to_contain_text('提交结果未知',timeout=15000)
                        page.unroute(confirm_pattern,lost_response)
                        expect(page.locator('[data-batch-confirm]')).to_be_disabled()
                        retained=page.evaluate("JSON.parse(localStorage.getItem('paam.import.pending.v1'))")
                        assert len(retained['rows'])==len(retained['intents'])==len(writes[-1]['selected_rows'])
                        persisted=snapshot();submitted_count=len(writes)
                        reconcile_pattern='**/paam/import/v1/import_file/reconcile'
                        def failed_observation(route):
                            route.fulfill(status=503,content_type='application/json',body=json.dumps(dict(status=503,
                                message='Mock observation failure',body=dict(code='QUERY_BUSY'))))
                        page.route(reconcile_pattern,failed_observation)
                        page.locator('[data-batch-verify]').click()
                        expect(page.locator('[data-batch-verify]')).to_be_enabled(timeout=15000)
                        expect(page.locator('[data-batch-observed]')).to_have_count(0)
                        assert page.evaluate("JSON.parse(localStorage.getItem('paam.import.pending.v1'))")==retained
                        page.unroute(reconcile_pattern,failed_observation)
                        def wrong_target(route):
                            response=route.fetch();value=response.json()
                            value['body']['items'][0]['target_transaction_id']=999999
                            route.fulfill(response=response,json=value)
                        page.route(reconcile_pattern,wrong_target)
                        page.locator('[data-batch-verify]').click()
                        expect(page.locator('[data-batch-verify]')).to_be_enabled(timeout=15000)
                        expect(page.locator('[data-batch-observed]')).to_have_count(0)
                        page.unroute(reconcile_pattern,wrong_target)
                        with page.expect_response(lambda response:response.url.endswith('/import_file/reconcile')) as read:
                            page.locator('[data-batch-verify]').click()
                        observed=read.value.json()['body']
                        assert observed['fully_observed'] and observed['current_state_only']
                        assert len(observed['items'])==len(retained['rows'])
                        expect(page.locator('[data-batch-observed]')).to_be_enabled(timeout=15000)
                        expect(page.locator('[data-reconcile-rows] .import-bulk-record')).to_have_count(20)
                        page.locator('[data-reconcile-rows] [data-bulk-next]').click()
                        expect(page.locator('[data-reconcile-rows] .import-bulk-record')).to_have_count(len(retained['rows'])-20)
                        if retained['intents'][0]['resolution']=='DUPLICATE':
                            assert all(item['state']=='DUPLICATE_EXCLUDED' and item['target_transaction_id'] in seed_ids for item in observed['items'])
                            assert {value['review_status'] for value in observed['outputs']}=={'CONFIRMED','REVOKED'}
                            expect(page.locator('[data-reconcile-rows]')).to_contain_text('不是数据库首次配对回执')
                        else:
                            assert all(item['state']=='EVIDENCE_LINKED' and item['transaction_id']==item['target_transaction_id'] for item in observed['items'])
                        assert len(writes)==submitted_count and snapshot()==persisted
                        assert page.evaluate("JSON.parse(localStorage.getItem('paam.import.pending.v1'))")==retained
                        viewport_evidence(page,'fix-import-reconcile-'+retained['intents'][0]['resolution'])
                        page.locator('[data-batch-observed]').click()
                        expect(page.locator('[data-batch-selection]')).to_contain_text('本次明确选择 0 行',timeout=15000)

                    def upload(files):
                        page.goto(base+'/#workbench/import')
                        page.locator('.import-step[data-step="2"]').click()
                        form=page.locator('[data-form="import-preview"]')
                        form.locator('[name="files"]').set_input_files(files)
                        with page.expect_response(lambda response:response.request.method=='POST'
                                and response.url.endswith('/paam/import/v1/preview'),timeout=35000) as uploaded:
                            form.locator('[data-action="preview-import"]').click()
                        response=uploaded.value
                        assert response.status==200,(response.status,response.text())
                        try:
                            expect(page.locator('[data-batch-row]')).to_have_count(20,timeout=30000)
                        except AssertionError as error:
                            raise AssertionError(f'Upload succeeded but draft rows did not mount; page errors={errors}; '
                                f'visible UI={page.locator("body").inner_text()[:4000]}') from error
                        expect(page.locator('[data-batch-select-scope]')).to_be_enabled(timeout=15000)
                        page.locator('[data-batch-select-scope]').click()
                        expect(page.locator('[data-batch-pair]')).to_be_enabled(timeout=15000)
                        # Risk defaults are SKIP; explicitly request accepted
                        # drafts before asking for pairing suggestions. This
                        # grants no NEW cash consent and retains all negatives.
                        page.locator('[data-batch-bulk]').click()
                        bulk=page.locator('dialog[open]')
                        bulk.locator('[data-bulk-action]').select_option('ACCEPT')
                        bulk.locator('[data-bulk-apply]').click()
                        expect(page.locator('[data-batch-pair]')).to_be_enabled(timeout=15000)

                    def open_pair(kind='SAME_SOURCE'):
                        expect(page.locator('[data-batch-pair]')).to_be_enabled(timeout=15000)
                        page.locator('[data-batch-pair]').click()
                        current=page.locator('dialog.import-pairing-dialog[open]')
                        current.locator('[data-pairing-kind]').select_option(kind)
                        current.locator('[data-pairing-read]').click()
                        return current

                    files=[{'name':'Mock-same.csv','mimeType':'text/csv','buffer':statement('990000000000001234',list(range(1,31))+[999],'Mock补证据')},
                        {'name':'Mock-unknown.csv','mimeType':'text/csv','buffer':statement('**************0000',[1],'Mock未知来源')},
                        {'name':'Mock-unmatched.csv','mimeType':'text/csv','buffer':statement('990000000000001234',[777],'Mock无匹配')}]
                    upload(files)
                    expect(page.locator('[data-batch-selection]')).to_contain_text('33 行')
                    before=snapshot() # File metadata may be created only by upload.
                    dialog=open_pair()
                    expect(dialog.locator('[data-pairing-count]')).to_contain_text('唯一建议 30 行，已选映射 0 行',timeout=15000)
                    expect(dialog.locator('[data-pairing-choice]:checked')).to_have_count(0)
                    expect(dialog.locator('[data-pairing-apply]')).to_be_disabled()
                    expect(dialog.locator('[data-pairing-row]')).to_have_count(20)
                    dialog.locator('[data-pairing-filter]').select_option('SUGGESTED')
                    expect(dialog.locator('[data-pairing-row]').first).to_contain_text('Mock原事实1')
                    expect(dialog.locator('[data-pairing-row]').first).to_contain_text('Fact #')
                    assert '990000000000001234' not in dialog.inner_text()
                    dialog.locator('[data-pairing-next]').click()
                    expect(dialog.locator('[data-pairing-page]')).to_contain_text('21–30 / 30')
                    expect(dialog.locator('[data-pairing-row]')).to_have_count(10)
                    dialog.locator('[data-pairing-filter]').select_option('ALL')
                    dialog.locator('[data-pairing-next]').click()
                    expect(dialog.locator('[data-pairing-page]')).to_contain_text('21–33 / 33')
                    expect(dialog.locator('[data-pairing-row]')).to_have_count(13)
                    dialog.locator('[data-pairing-filter]').select_option('OTHER')
                    expect(dialog.locator('[data-pairing-row]')).to_have_count(3)
                    expect(dialog.locator('[data-pairing-items]')).to_contain_text('有多个候选')
                    expect(dialog.locator('[data-pairing-items]')).to_contain_text('完整来源身份不能证明')
                    expect(dialog.locator('[data-pairing-items]')).to_contain_text('不自动视为新交易')
                    dialog.locator('[data-pairing-all]').click()
                    expect(dialog.locator('[data-pairing-count]')).to_contain_text('已选映射 30 行')
                    dialog.locator('[data-workbench-close]').click() # Closing even selected suggestions does nothing.
                    assert puts==writes==[] and snapshot()==before

                    pattern='**/paam/import/v1/preview/*/pairing-preview'
                    def fail_read(route):
                        route.fulfill(status=503,content_type='application/json',body=json.dumps(dict(status=503,message='Mock read failure',body=dict(code='QUERY_BUSY'))))
                    page.route(pattern,fail_read)
                    dialog=open_pair()
                    expect(dialog.locator('[data-pairing-status]')).to_contain_text('QUERY_BUSY')
                    expect(dialog.locator('[data-pairing-apply]')).to_be_disabled()
                    dialog.locator('[data-workbench-close]').click();page.unroute(pattern,fail_read)
                    def wrong_time(route):
                        response=route.fetch();value=response.json();value['body']['expected_updated_time']='2026-10-03T00:00:00.000001Z'
                        route.fulfill(response=response,json=value)
                    page.route(pattern,wrong_time)
                    dialog=open_pair()
                    expect(dialog.locator('[data-pairing-status]')).to_contain_text('前提不一致')
                    expect(dialog.locator('[data-pairing-apply]')).to_be_disabled()
                    dialog.locator('[data-workbench-close]').click();page.unroute(pattern,wrong_time)
                    # An actual whole response held after the server read cannot
                    # revive suggestions after stopping or changing strategy.
                    held=[]
                    def hold_read(route):
                        held.append((route,route.fetch()))
                    page.route(pattern,hold_read)
                    dialog=open_pair()
                    expect(dialog.locator('[data-pairing-stop]')).to_be_enabled()
                    for _ in range(100):
                        if held:break
                        page.wait_for_timeout(20)
                    assert held
                    dialog.locator('[data-pairing-stop]').click()
                    expect(dialog.locator('[data-pairing-status]')).to_contain_text('已取消')
                    for route,response in held:
                        try:route.fulfill(response=response)
                        except BrowserError:pass # Browser may have already aborted this readonly transport.
                    page.unroute(pattern,hold_read)
                    expect(dialog.locator('[data-pairing-apply]')).to_be_disabled()
                    expect(dialog.locator('[data-pairing-row]')).to_have_count(0)
                    dialog.locator('[data-workbench-close]').click()
                    assert puts==writes==[] and snapshot()==before

                    dialog=open_pair()
                    expect(dialog.locator('[data-pairing-count]')).to_contain_text('唯一建议 30 行',timeout=15000)
                    # Complete ambiguous rows remain reachable without removing
                    # them from the operation. Existing named per-row picker is reused.
                    dialog.locator('[data-pairing-filter]').select_option('OTHER')
                    expect(dialog.locator('[data-pairing-row]')).to_have_count(3)
                    ambiguous=dialog.locator('[data-pairing-row]').filter(has_text='Mock补证据31')
                    ambiguous.locator('[data-pairing-manual]').click()
                    child=page.locator('dialog[open]').last
                    child.locator('[data-import-resolution]').select_option('LINK_EXISTING')
                    expect(child.locator('[data-match-items] .picker-list-row')).to_have_count(2)
                    expect(child.locator('[data-intent-apply]')).to_be_disabled()
                    child.locator('[data-match-id]').first.click()
                    child.locator('[data-intent-apply]').click()
                    expect(dialog.locator('[data-pairing-status]')).to_contain_text('须重新核验完整映射')
                    expect(dialog.locator('[data-pairing-apply]')).to_be_disabled()
                    assert puts==writes==[] and snapshot()==before
                    dialog.locator('[data-pairing-read]').click()
                    expect(dialog.locator('[data-pairing-count]')).to_contain_text('人工草稿 1 行',timeout=15000)
                    expect(dialog.locator('[data-pairing-count]')).to_contain_text('唯一建议 30 行')
                    dialog.locator('[data-pairing-filter]').select_option('ALL')
                    dialog.locator('[data-pairing-all]').click()
                    dialog.locator('[data-pairing-apply]').click()
                    expect(dialog.locator('[data-pairing-status]')).to_contain_text('请明确确认')
                    dialog.locator('[data-pairing-ack]').check()
                    dialog.locator('[data-pairing-apply]').click()
                    expect(dialog.locator('[data-pairing-status]')).to_contain_text('其余行保留')
                    for width in (1440,1280,820,390):
                        page.set_viewport_size({'width':width,'height':900})
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        assert dialog.evaluate('node=>node.scrollWidth <= node.clientWidth')
                        viewport_evidence(page,f'fix-import-pairing-link-{width}')
                    dialog.locator('[data-pairing-retain-ack]').check()
                    dialog.locator('[data-pairing-apply]').click()
                    expect(dialog).to_have_count(0)
                    expect(page.locator('[data-batch-status]')).to_contain_text('已应用 31 行具名配对／人工草稿；其余 2 行保留')
                    assert puts==writes==[] and snapshot()==before
                    page.set_viewport_size({'width':1280,'height':900})
                    expect(page.locator('[data-batch-save]')).to_be_enabled(timeout=15000)
                    page.locator('[data-batch-save]').click()
                    expect(page.locator('[data-batch-plan]')).to_be_enabled(timeout=15000)
                    assert len(puts)==1 and len(puts[0]['choices'])==33
                    assert sum(row.get('resolution','AUTO')=='LINK_EXISTING' for row in puts[0]['choices'])==31
                    assert all(not row.get('acknowledge_new_risk',False) for row in puts[0]['choices'])
                    assert len({row['target']['transaction_id'] for row in puts[0]['choices'] if row.get('resolution','AUTO')=='LINK_EXISTING'})==31
                    page.locator('[data-batch-plan]').click()
                    expect(page.locator('[data-import-operation]')).to_contain_text('未解决',timeout=15000)
                    expect(page.locator('[data-batch-confirm]')).to_be_disabled()
                    assert writes==[] and snapshot()==before
                    # User explicitly deselects unresolved source rows. No UI
                    # confirmation implicitly excluded them from accounting.
                    for filename in ('Mock-unknown.csv','Mock-unmatched.csv'):
                        option=page.locator('[data-batch-file] option').filter(has_text=filename).get_attribute('value')
                        page.locator('[data-batch-file]').select_option(option)
                        expect(page.locator('[data-batch-row]')).to_have_count(1,timeout=15000)
                        current_row=page.locator(f'[data-batch-row^="{option}:"]')
                        expect(current_row).to_have_count(1,timeout=15000)
                        current_row.locator('[data-row-select]').uncheck()
                    expect(page.locator('[data-batch-selection]')).to_contain_text('31 行')
                    page.locator('[data-batch-save]').click()
                    expect(page.locator('[data-batch-plan]')).to_be_enabled(timeout=15000)
                    page.locator('[data-batch-plan]').click()
                    expect(page.locator('[data-import-operation]')).to_contain_text('可规划 1 批',timeout=15000)
                    expect(page.locator('[data-batch-confirm]')).to_be_enabled()
                    confirm_visible_batch()
                    assert len(writes)==len(responses)==1
                    assert responses[0]['manual_linked_count']==31
                    assert all(row['transaction_id'] in seed_ids and row['created_review_id']==row['created_ledger_id']==0 for row in responses[0]['processed_rows'])
                    linked=snapshot()
                    for name in before:
                        if name not in ('transaction_import_file','transaction_import_row'):assert linked[name]==before[name],name

                    upload([{'name':'Mock-cross.csv','mimeType':'text/csv','buffer':statement('990000000000009876',list(range(1,31)),'Mock跨源B')}])
                    before_cross=snapshot()
                    dialog=open_pair('CROSS_SOURCE')
                    expect(dialog.locator('[data-pairing-count]')).to_contain_text('唯一建议 30 行，已选映射 0 行',timeout=15000)
                    dialog.locator('[data-pairing-all]').click();dialog.locator('[data-pairing-ack]').check()
                    expect(dialog.locator('[data-pairing-retain]')).to_be_hidden()
                    viewport_evidence(page,'fix-import-pairing-cross-1280')
                    dialog.locator('[data-pairing-apply]').click()
                    expect(page.locator('[data-batch-save]')).to_be_enabled(timeout=15000)
                    page.locator('[data-batch-save]').click()
                    expect(page.locator('[data-batch-plan]')).to_be_enabled(timeout=15000)
                    assert all(row['resolution']=='DUPLICATE' and not row.get('acknowledge_new_risk',False) for row in puts[-1]['choices'])
                    assert snapshot()==before_cross
                    page.locator('[data-batch-plan]').click()
                    expect(page.locator('[data-import-operation]')).to_contain_text('可规划 1 批',timeout=15000)
                    page.locator('[data-plan-batch]').click()
                    expect(page.locator('[data-plan-batch-detail]')).to_contain_text('新重复 Fact 30')
                    confirm_visible_batch()
                    assert len(writes)==len(responses)==2
                    processed=responses[-1]['processed_rows']
                    assert len(processed)==30 and len({row['duplicate_kept_transaction_id'] for row in processed})==30
                    assert all(row['duplicate_kept_transaction_id'] in seed_ids and row['transaction_id'] not in seed_ids for row in processed)
                    after=snapshot()
                    assert len(after['transaction_fact'])-len(before_cross['transaction_fact'])==30
                    assert len(after['review_case'])-len(before_cross['review_case'])==31
                    assert len(after['ledger_entry'])-len(before_cross['ledger_entry'])==60
                    for name in ('transaction_fact','review_case','ledger_entry','ledger_entry_tag'):
                        assert after[name][:len(before_cross[name])]==before_cross[name],name
                    assert page.evaluate("localStorage.getItem('paam.import.pending.v1')") is None
                    assert errors==[] and provider_calls==[],(errors,provider_calls)
                    assert all(len(request['choices'])==33 for request in pairs[:-1])
                    browser.close()
            print('PASS complete 33-row named mapping, 30 unique suggestions no consent, ambiguity/unknown/no-match retained, cancel/failure/stale/abort-late reads, manual named choice and complete reread, four widths; real 31 LINK and 30 DUP atomic writes, unchanged keepers, provider calls=0')
        finally:
            server.should_exit=True;worker.join(timeout=10);target_database.engine.dispose()


if __name__=='__main__':run()
