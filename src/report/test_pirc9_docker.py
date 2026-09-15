"""Browser/HTTP acceptance against the dedicated Docker review service.

Run explicitly; only synthetic data is added, via public HTTP APIs and UI.
The container is retained for inspection. No direct database seeding is used.
"""
import base64
import os
from pathlib import Path
import subprocess
from uuid import uuid4

import httpx
import pytest
from playwright.sync_api import sync_playwright, expect

from test_pirc9_regression import (
    body,
    test_navigation_ignores_late_previous_page_response,
    test_import_preview_survives_navigation,
)

URL = os.getenv('PIRC9_TEST_URL', 'http://127.0.0.1:18765')
CONTAINER = os.getenv('PIRC9_TEST_CONTAINER', 'paam-pirc9-review-fd82f6ef')


@pytest.fixture(scope='module')
def client():
    label = subprocess.check_output([
        'docker', 'inspect', '--format', '{{index .Config.Labels "paam.purpose"}}', CONTAINER
    ], text=True).strip()
    assert label == 'pirc9-disposable-review', 'Refuse to seed an unmarked service'
    with httpx.Client(base_url=URL, timeout=30) as client:
        assert client.get('/api/health').json()['schema'] == 'pirc-9-target'
        yield client


@pytest.fixture
def page(client, request):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, locale='zh-CN')
        page.set_default_timeout(5000)
        page.goto(URL)
        expect(page.locator('[data-form="ledger-filter"]')).to_be_visible()
        yield page
        directory = Path(__file__).parent / 'docker-evidence'
        directory.mkdir(exist_ok=True)
        page.screenshot(path=str(directory / f'{request.node.name}.png'), full_page=True)
        browser.close()


def statement(specs, day='2026-08-01'):
    name = f'docker-{uuid4().hex[:12]}'
    text = ('微信支付账单明细列表\n'
            '交易时间,交易类型,交易对手,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注\n')
    for i, (direction, value) in enumerate(specs):
        sign = '收入' if direction == 'IN' else '支出'
        text += f'{day} 10:00:{i:02d},商户消费,{name},验收,{sign},{value / 100:.2f},零钱,支付成功,{name}-{i},m-{name}-{i},Docker验收\n'
    return name, text.encode()


def imported(client, specs, day='2026-08-01'):
    name, content = statement(specs, day)
    plan = body(client.post('/paam/import/v1/preview', json={'files': [{
        'filename': f'{name}.csv', 'content_base64': base64.b64encode(content).decode()}]}))
    assert plan['can_confirm'], plan
    result = body(client.post(f'/paam/import/v1/preview/confirm/{plan["token"]}', json={'version': plan['version']}))
    ids = result['bill_fact_ids']
    assert len(ids) == len(specs)
    return name, ids


def create(client, kind, ids, roles, amounts=None):
    lines = [{'bill_id': fid, 'role': role} for fid, role in zip(ids, roles)]
    if amounts:
        for line, value in zip(lines, amounts):
            line['amount_value'] = value
    return body(client.post('/paam/review/v1/case/create', json={
        'review_type': kind, 'title': '', 'lines': lines, 'idempotency_key': uuid4().hex}))


def change(client, case, kind):
    return body(client.post(f'/paam/review/v1/case/{kind}/{case["id"]}', json={
        'expected_version': case['version'], 'idempotency_key': uuid4().hex}))


def ledger(client, name):
    return body(client.get('/paam/ledger/v1/entry/list', params={'q': name}))['items']


def open_detail(page, name):
    page.goto(f'{URL}/#ledger?q={name}&date_from=2026-08-01&date_to=2026-09-30')
    page.locator('.ledger-table-primary[data-action="detail"]').first.click()


def open_review(page, case):
    page.goto(f'{URL}/#workbench?task=reviews&view=queue')
    page.locator(f'[data-action="review-detail"][data-id="{case["id"]}"]').click()


def open_review_wizard(page, name, review_type='AA'):
    page.goto(f'{URL}/#ledger?q={name}&date_from=2026-08-01&date_to=2026-09-30')
    page.locator('[data-action="ledger-select"]').first.check()
    page.locator('.review-selection-bar [data-action="review-selected"]').click()
    wizard = page.locator('[data-form="review-wizard"]')
    wizard.locator('[data-review-next]').click()
    wizard.locator('[name="review_type"]').select_option(review_type)
    wizard.locator('[data-review-next]').click()
    wizard.locator('[data-review-next]').click()
    return wizard


def test_partial_refund_uses_allocated_amount(client):
    _, ids = imported(client, [('IN', 10000), ('OUT', 30000)], '2026-08-02')
    change(client, create(client, 'REFUND', ids, ['REFUND_RECEIVED', 'REFUND_EXPENSE'], [6000, 6000]), 'confirm')
    total = body(client.get('/paam/ledger/v1/summary?date_from=2026-08-02&date_to=2026-08-02'))['totals'][0]
    assert total['refund_offset_value'] == 6000, total


def test_aa_summary_preserves_individual_case_income_expense(client):
    _, ids = imported(client, [('OUT', 10000), ('IN', 6000), ('OUT', 5000), ('IN', 10000)], '2026-08-03')
    change(client, create(client, 'AA', ids[:2], ['AA_PAID', 'AA_RECEIVED']), 'confirm')
    change(client, create(client, 'AA', ids[2:], ['AA_PAID', 'AA_RECEIVED']), 'confirm')
    total = body(client.get('/paam/ledger/v1/summary?date_from=2026-08-03&date_to=2026-08-03'))['totals'][0]
    assert (total['income_value'], total['expense_value']) == (5000, 4000), total


def test_nested_revoke_refreshes_parent_and_allows_next_operation(client, page):
    name, ids = imported(client, [('OUT', 1000)])
    change(client, create(client, 'AA', ids, ['AA_PAID']), 'confirm')
    open_detail(page, name)
    page.locator('[data-action="review-detail"]').click()
    page.locator('[data-kind="revoke"]').click()
    expect(page.locator('dialog')).to_have_count(0)
    open_detail(page, name)
    page.locator('dialog[open] [data-action="account"]').click()
    page.locator('[name="account_code"]').fill('docker-after-revoke')
    with page.expect_response('**/paam/review/v1/account/set/*') as result:
        page.locator('[data-form="account"] button.primary').click()
    assert result.value.status == 200, result.value.text()


def test_revoked_review_exposes_edit(client, page):
    _, ids = imported(client, [('OUT', 1000)])
    case = change(client, change(client, create(client, 'AA', ids, ['AA_PAID']), 'confirm'), 'revoke')
    open_review(page, case)
    expect(page.locator('[data-action="edit-review"]')).to_be_visible()


def test_account_edit_reopens_with_effective_value(client, page):
    name, ids = imported(client, [('OUT', 1000)])
    open_detail(page, name)
    page.locator('dialog[open] [data-action="account"]').click()
    page.locator('[name="account_code"]').fill('docker-corrected-wallet')
    page.locator('[data-form="account"] button.primary').click()
    expect(page.locator('dialog')).to_have_count(0)
    assert ledger(client, name)[0]['out_account_code'] == 'docker-corrected-wallet'
    open_detail(page, name)
    page.locator('dialog[open] [data-action="account"]').click()
    assert page.locator('[name="account_code"]').input_value() == 'docker-corrected-wallet'


def test_double_submit_is_one_command(client, page):
    name, ids = imported(client, [('OUT', 1000)])
    wizard = open_review_wizard(page, name)
    wizard.evaluate('(form) => {form.requestSubmit(); form.requestSubmit();}')
    expect(page.locator('[data-action="review-transition"][data-kind="confirm"]')).to_be_visible()
    page.wait_for_timeout(300)
    cases = body(client.get('/paam/review/v1/case/list?limit=200'))
    matching = [case for case in cases if case['review_type'] == 'AA' and any(line['bill_id'] == ids[0] for line in case['lines'])]
    assert len(matching) == 1, f'Created {len(matching)} cases for one double submission'


def test_tag_restore_after_merge_returns_actionable_result(client, page):
    name, ids = imported(client, [('OUT', 1000), ('IN', 1000)])
    system = f'category_{uuid4().hex[:8]}'
    view = body(client.post('/paam/tag/v1/view/create', json={'name': 'Docker archive conflict', 'system_name': system}))
    body(client.post(f'/paam/tag/v1/tag/create/{view["id"]}', json={'name': 'Food', 'system_name': 'food'}))
    entry = ledger(client, name)[0]
    views = body(client.get('/paam/tag/v1/view/list'))
    state = {view['system_name']: 'unclassified' for view in views}
    state[system] = 'food'
    body(client.put(f'/paam/tag/v1/assignment/set/{entry["id"]}', json={
        'tag_state': state, 'expected_projection_version': entry['projection_version'], 'idempotency_key': uuid4().hex}))
    body(client.put(f'/paam/tag/v1/view/status/{view["id"]}', json={'status': 'ARCHIVED'}))
    change(client, create(client, 'TRANSFER', ids, ['TRANSFER_OUT', 'TRANSFER_IN']), 'confirm')
    page.locator('nav [data-page="tags"]').click()
    with page.expect_response(f'**/paam/tag/v1/view/status/{view["id"]}') as response:
        page.locator(f'[data-action="view-status"][data-id="{view["id"]}"]').click()
    assert response.value.status in (200, 409, 422), response.value.text()


def test_ui_import_tag_account_review_and_duplicate_import(client, page):
    before_totals = body(client.get('/paam/ledger/v1/summary?date_from=2026-08-04&date_to=2026-08-04'))['totals']
    before_expense = sum(item['expense_value'] for item in before_totals if item['currency_code'] == 'CNY')
    name, content = statement([('OUT', 1234)], '2026-08-04')
    page.locator('[data-page="import"]').first.click()
    page.locator('[data-action="import-step"][data-step="2"]').first.click()
    page.locator('input[name="files"]').set_input_files({'name': f'{name}.csv', 'mimeType': 'text/csv', 'buffer': content})
    page.locator('[data-action="preview-import"]').click()
    expect(page.locator('[data-action="confirm-import"]')).to_be_enabled()
    page.locator('[data-action="confirm-import"]').click()
    expect(page.locator('[data-form="history-filter"]')).to_be_visible()
    expect(page.get_by_text(f'{name}.csv', exact=True)).to_be_visible()
    entry = ledger(client, name)[0]
    detail = body(client.get(f'/paam/ledger/v1/entry/detail/{entry["id"]}'))
    fid = detail['facts'][0]['id']
    page.locator('nav [data-page="tags"]').click()
    page.locator('[data-action="new-view"]').click()
    system = f'ui_{uuid4().hex[:8]}'
    page.locator('dialog [name="name"]').fill('Docker UI dimension')
    page.locator('dialog [name="system_name"]').fill(system)
    page.locator('[data-form="dictionary"] button.primary').click()
    expect(page.locator('dialog')).to_have_count(0)
    view = next(v for v in body(client.get('/paam/tag/v1/view/list')) if v['system_name'] == system)
    page.locator(f'[data-action="new-tag"][data-id="{view["id"]}"]').click()
    inline_tag = page.locator(f'[data-form="inline-tag"][data-view="{view["id"]}"]')
    inline_tag.locator('[name="name"]').fill('Docker selected tag')
    inline_tag.locator('[name="system_name"]').fill('selected')
    inline_tag.locator('[type="submit"]').click()
    expect(page.locator(f'[aria-labelledby="tag-view-{view["id"]}"]').get_by_text('Docker selected tag', exact=True)).to_be_visible()
    open_detail(page, name)
    page.locator('dialog[open] [data-action="edit-tags"]').click()
    page.locator(f'select[name="{system}"]').select_option('selected')
    page.locator('[data-form="tag-assignment"] button.primary').click()
    expect(page.locator('dialog')).to_have_count(0)
    assert any(tag['tag_system_name'] == 'selected' for tag in ledger(client, name)[0]['tags'])
    tag = next(t for t in body(client.get('/paam/tag/v1/view/list')) if t['system_name'] == system)['tags']
    tag_id = next(t['id'] for t in tag if t['system_name'] == 'selected')
    page.locator('nav [data-page="tags"]').click()
    for target, expected in [('ARCHIVED', 'unclassified'), ('ACTIVE', 'selected')]:
        page.locator(f'[data-action="tag-status"][data-id="{tag_id}"][data-status="{target}"]').click()
        expect(page.locator(f'[data-action="tag-status"][data-id="{tag_id}"][data-status="{target}"]')).to_have_count(0)
        assert next(t for t in ledger(client, name)[0]['tags'] if t['view_system_name'] == system)['tag_system_name'] == expected
    for target, present in [('ARCHIVED', False), ('ACTIVE', True)]:
        page.locator(f'[data-action="view-status"][data-id="{view["id"]}"][data-status="{target}"]').click()
        expect(page.locator(f'[data-action="view-status"][data-id="{view["id"]}"][data-status="{target}"]')).to_have_count(0)
        assert any(t['view_system_name'] == system for t in ledger(client, name)[0]['tags']) == present
    open_detail(page, name)
    page.locator('dialog[open] [data-action="account"]').click()
    page.locator('[name="account_code"]').fill('docker-ui-wallet')
    page.locator('[data-form="account"] button.primary').click()
    expect(page.locator('dialog')).to_have_count(0)
    assert ledger(client, name)[0]['out_account_code'] == 'docker-ui-wallet'
    account_case = next(c for c in body(client.get('/paam/review/v1/case/list?limit=200'))
                        if c['review_type'] == 'ACCOUNT' and c['lines'][0]['bill_id'] == fid)
    original_account = detail['facts'][0]['account_code']
    for action, account in [('revoke', original_account), ('restore', 'docker-ui-wallet')]:
        open_review(page, account_case)
        page.locator(f'[data-action="account-transition"][data-kind="{action}"]').click()
        expect(page.locator('dialog')).to_have_count(0)
        assert ledger(client, name)[0]['out_account_code'] == account
    wizard = open_review_wizard(page, name)
    wizard.locator('[data-review-submit]').click()
    case = next(c for c in body(client.get('/paam/review/v1/case/list?limit=200'))
                if c['review_type'] == 'AA' and c['lines'][0]['bill_id'] == fid)
    for index, (action, expected_type) in enumerate([('confirm', 'AA'), ('revoke', 'EXPENSE'), ('restore', 'AA')]):
        if index:
            open_review(page, case)
        page.locator(f'[data-action="review-transition"][data-kind="{action}"]').click()
        expect(page.locator('dialog')).to_have_count(0)
        case = body(client.get(f'/paam/review/v1/case/detail/{case["id"]}'))
        assert ledger(client, name)[0]['ledger_type'] == expected_type
        assert ledger(client, name)[0]['out_account_code'] == 'docker-ui-wallet'
    plan = body(client.post('/paam/import/v1/preview', json={'files': [{
        'filename': f'renamed-{name}.csv', 'content_base64': base64.b64encode(content).decode()}]}))
    body(client.post(f'/paam/import/v1/preview/confirm/{plan["token"]}', json={'version': plan['version']}))
    assert len(ledger(client, name)) == 1
    totals = body(client.get('/paam/ledger/v1/summary?date_from=2026-08-04&date_to=2026-08-04'))['totals']
    assert sum(item['expense_value'] for item in totals if item['currency_code'] == 'CNY') == before_expense + 1234
    page.goto(f'{URL}/#ledger?q={name}&date_from=2026-08-01&date_to=2026-09-30')
    expect(page.locator('.ledger-table-primary[data-action="detail"]')).to_have_count(1)


def test_ledger_filter_and_pagination(client, page):
    name, _ = imported(client, [('OUT', 100 + i) for i in range(30)], '2026-08-05')
    page.goto(f'{URL}/#ledger?q={name}&date_from=2026-08-01&date_to=2026-09-30')
    expect(page.locator('.ledger-table-primary[data-action="detail"]')).to_have_count(25)
    page.locator('[data-action="page"][aria-label="下一页"]').click()
    expect(page.locator('.ledger-table-primary[data-action="detail"]')).to_have_count(5)
    page.locator('[data-action="page"][aria-label="上一页"]').click()
    expect(page.locator('.ledger-table-primary[data-action="detail"]')).to_have_count(25)
    page.locator('[data-form="ledger-filter"] [name="q"]').fill(f'{name}-absent')
    page.locator('[data-form="ledger-filter"]').evaluate('(form) => form.requestSubmit()')
    expect(page.locator('.ledger-table-primary[data-action="detail"]')).to_have_count(0)
    result = body(client.get('/paam/ledger/v1/entry/list', params={'q': name, 'page_size': 100}))
    assert result['total'] == 30


def test_invalid_preview_does_not_write_ledger(client):
    before = body(client.get('/paam/ledger/v1/entry/list'))['total']
    plan = body(client.post('/paam/import/v1/preview', json={'files': [{
        'filename': 'invalid-docker.csv', 'content_base64': base64.b64encode(b'not,a,statement').decode()}]}))
    assert not plan['can_confirm']
    response = client.post(f'/paam/import/v1/preview/confirm/{plan["token"]}', json={'version': plan['version']})
    assert response.status_code == 422
    assert body(client.get('/paam/ledger/v1/entry/list'))['total'] == before


def test_fact_conflict_dismiss_reopen_resolve_via_ui(client, page):
    name, content = statement([('OUT', 1000)], '2026-08-06')
    for suffix, data in [('original', content), ('conflict', content.replace(b',10.00,', b',20.00,'))]:
        plan = body(client.post('/paam/import/v1/preview', json={'files': [{
            'filename': f'{name}-{suffix}.csv', 'content_base64': base64.b64encode(data).decode()}]}))
        assert plan['can_confirm'], plan
        body(client.post(f'/paam/import/v1/preview/confirm/{plan["token"]}', json={'version': plan['version']}))
    original_entry = ledger(client, name)[0]
    case = next(c for c in body(client.get('/paam/review/v1/case/list?limit=200')) if c['review_type'] == 'FACT_CONFLICT')
    for action, status in [('dismiss', 'REJECTED'), ('reopen', 'PENDING')]:
        open_review(page, case)
        page.locator(f'[data-action="conflict-transition"][data-kind="{action}"]').click()
        expect(page.locator('dialog')).to_have_count(0)
        assert body(client.get(f'/paam/review/v1/case/detail/{case["id"]}'))['status'] == status
    open_review(page, case)
    page.locator('[data-action="conflict-resolve"]').click()
    page.locator('[name="resolution_type"]').select_option('CREATE_NEW')
    with page.expect_response(f'**/paam/review/v1/fact-conflict/resolve/{case["id"]}') as response:
        page.locator('[data-form="conflict"] button.primary').click()
    assert response.value.status == 200, response.value.text()
    resolved = body(client.get(f'/paam/review/v1/case/detail/{case["id"]}'))
    assert resolved['status'] == 'CONFIRMED'
    assert [h['operation'] for h in resolved['history']][-3:] == ['DISMISS', 'REOPEN', 'RESOLVE']
    original = body(client.get(f'/paam/ledger/v1/entry/detail/{original_entry["id"]}'))
    assert original['facts'][0]['amount']['amount_value'] == 1000
    assert len(ledger(client, name)) == 2


def test_old_pending_case_remains_accessible_after_200_new_cases(client, page):
    # Added last so the large review queue does not obscure earlier scenarios.
    _, ids = imported(client, [('OUT', 1000)])
    oldest = create(client, 'AA', ids, ['AA_PAID'])
    for _ in range(200):
        create(client, 'AA', ids, ['AA_PAID'])
    page.locator('[data-module="workbench"]').click()
    page.locator('[data-action="review-queue"]').click()
    page.locator('[data-form="review-filter"] [name="status"]').select_option('PENDING')
    page.locator('[data-form="review-filter"]').evaluate('(form) => form.requestSubmit()')
    target = page.locator(f'[data-action="review-detail"][data-id="{oldest["id"]}"]')
    while target.count() == 0:
        current = int(page.url.split('page=')[1].split('&')[0])
        next_button = page.locator(f'[data-action="review-page"][data-param="review_page"][data-value="{current + 1}"]:not([disabled])')
        if next_button.count() == 0:
            break
        next_button.click()
        expect(page.locator('.review-dashboard > .panel .pagination').last).to_contain_text(f'第 {current + 1}/')
    assert target.count() == 1, 'Pending review is not reachable through status filtering and pagination'


def test_dialog_error_is_visible_above_modal_backdrop(client, page):
    name, _ = imported(client, [('OUT', 1000)])
    wizard = open_review_wizard(page, name)
    wizard.evaluate('(form) => form.closest("dialog").reviewFacts[0].id = 0')
    with page.expect_response('**/paam/review/v1/case/create') as response:
        wizard.locator('[data-review-submit]').click()
    assert response.value.status == 422
    expect(page.locator('.toast.error')).to_have_count(1)
    unobscured = page.locator('.toast.error').evaluate('''node => {
        const rect = node.getBoundingClientRect();
        const top = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
        return top === node || node.contains(top);
    }''')
    assert unobscured, 'Validation error is appended to body, behind the open dialog and its blurred backdrop'
