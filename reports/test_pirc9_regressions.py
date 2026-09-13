"""Review probes: run explicitly; failures describe unmet user-visible behavior.

    .venv/Scripts/python.exe -m pytest reports/test_pirc9_regressions.py -q

All API/browser writes use disposable SQLite, never the development database.
"""
from datetime import datetime
from pathlib import Path
import socket
import sys
import threading
from uuid import uuid4

import httpx
import pytest
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import target_database
from app.models.target import BillFact, ReviewCase
from app.services.target_projection_service import TargetProjectionService


@pytest.fixture
def live(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'review.db'}", connect_args={"check_same_thread": False})
    sessions = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(target_database, "engine", engine)
    monkeypatch.setattr(target_database, "SessionLocal", sessions)
    from app.target_main import app
    import uvicorn
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=url) as client:
            for _ in range(100):
                if server.started:
                    break
                threading.Event().wait(.05)
            assert server.started
            yield client, sessions, url
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        engine.dispose()


@pytest.fixture
def page(live):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(3000)
        page.goto(live[2])
        expect(page.locator('[data-form="summary-filter"]')).to_be_visible()
        yield page
        context.close()
        browser.close()


def body(response):
    assert response.status_code == 200, response.text
    value = response.json()
    return value.get("body", value)


def facts(live, specs):
    now = datetime(2026, 9, 12, 10)
    with live[1]() as db:
        rows = [BillFact(fact_key=uuid4().hex, occurred_time=now,
                        cash_direction=direction, amount_value=value, amount_scale=2,
                        currency_code="CNY", account_code="original-wallet",
                        counterparty="probe", summary="probe", created_time=now,
                        updated_time=now) for direction, value in specs]
        db.add_all(rows)
        db.flush()
        ids = [row.id for row in rows]
        TargetProjectionService(db).rebuild_defaults(ids)
        db.commit()
        return ids


def create(live, kind, ids, roles, amounts=None):
    lines = [{"bill_id": fid, "role": role} for fid, role in zip(ids, roles)]
    if amounts:
        for line, amount in zip(lines, amounts):
            line["amount_value"] = amount
    return body(live[0].post('/paam/review/v1/case/create', json={
        "review_type": kind, "lines": lines, "idempotency_key": uuid4().hex}))


def transition(live, case, action):
    return body(live[0].post(f'/paam/review/v1/case/{action}/{case["id"]}', json={
        "expected_version": case["version"], "idempotency_key": uuid4().hex}))


def entries(live):
    return body(live[0].get('/paam/ledger/v1/entry/list'))['items']


def test_navigation_ignores_late_previous_page_response(page):
    held = []
    page.route('**/paam/ledger/v1/entry/list?*', lambda route: held.append(route))
    page.locator('nav [data-page="ledger"]').click()
    page.wait_for_timeout(150)
    assert held
    page.locator('nav [data-page="tags"]').click()
    expect(page.locator('[data-action="new-view"]')).to_be_visible()
    held[0].continue_()
    page.wait_for_timeout(300)
    assert page.locator('[data-action="new-view"]').count() == 1, 'Late ledger response replaced the tags page'


def test_revoked_review_has_edit_action(live, page):
    ids = facts(live, [('OUT', 1000)])
    case = create(live, 'AA', ids, ['AA_PAID'])
    case = transition(live, transition(live, case, 'confirm'), 'revoke')
    page.locator('nav [data-page="reviews"]').click()
    page.locator(f'[data-action="review-detail"][data-id="{case["id"]}"]').click()
    expect(page.locator('dialog [data-action="edit-review"]')).to_be_visible()


def test_nested_review_transition_refreshes_parent_detail(live, page):
    ids = facts(live, [('OUT', 1000)])
    case = transition(live, create(live, 'AA', ids, ['AA_PAID']), 'confirm')
    page.locator('nav [data-page="ledger"]').click()
    page.locator('[data-action="detail"]').click()
    page.locator('[data-action="review-detail"]').click()
    page.locator('[data-action="review-transition"][data-kind="revoke"]').click()
    expect(page.locator('dialog')).to_have_count(0)
    current = entries(live)[0]['projection_version']
    page.locator('[data-action="detail"]').first.click()
    shown = int(page.locator('[data-action="edit-tags"]').get_attribute('data-version'))
    assert shown == current, f'Reopened detail retains version {shown}; actual version is {current}'


def test_account_editor_prefills_effective_account(live, page):
    ids = facts(live, [('OUT', 1000)])
    entry = entries(live)[0]
    body(live[0].put(f'/paam/review/v1/account/set/{ids[0]}', json={
        'account_code': 'corrected-wallet', 'expected_projection_version': entry['projection_version'],
        'idempotency_key': uuid4().hex}))
    assert entries(live)[0]['out_account_code'] == 'corrected-wallet'
    page.locator('nav [data-page="ledger"]').click()
    page.locator('[data-action="detail"]').click()
    page.locator('[data-action="account"]').click()
    assert page.locator('[name="account_code"]').input_value() == 'corrected-wallet'


def test_import_preview_survives_navigation(page):
    page.locator('nav [data-page="import"]').click()
    page.locator('input[name="files"]').set_input_files({
        'name': 'probe.csv', 'mimeType': 'text/csv', 'buffer': (
            '微信支付账单明细列表\n'
            '交易时间,交易类型,交易对手,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注\n'
            '2026-09-12 10:00:00,商户消费,测试商户,午餐,支出,12.34,零钱,支付成功,probe-1,m-1,验证\n'
        ).encode()})
    page.locator('[data-form="import-preview"] button').click()
    expect(page.locator('[data-action="confirm-import"]')).to_be_enabled()
    page.locator('nav [data-page="tags"]').click()
    expect(page.locator('[data-action="new-view"]')).to_be_visible()
    page.locator('nav [data-page="import"]').click()
    expect(page.locator('[data-form="import-preview"]')).to_be_visible()
    assert page.locator('[data-action="confirm-import"]').count() == 1, 'Preview remains in state but cannot be confirmed after returning'


def test_double_submit_creates_one_review(live, page):
    ids = facts(live, [('OUT', 1000)])
    page.locator('nav [data-page="reviews"]').click()
    page.locator('[data-action="new-review"]').click()
    page.locator('[name="lines"]').fill(f'{ids[0]}:AA_PAID')
    page.locator('[data-form="new-review"]').evaluate('(form) => {form.requestSubmit(); form.requestSubmit();}')
    page.wait_for_timeout(500)
    cases = body(live[0].get('/paam/review/v1/case/list'))
    assert len(cases) == 1, f'Double submit created {len(cases)} distinct review cases'


def test_summary_does_not_net_unrelated_aa_cases(live):
    ids = facts(live, [('OUT', 10000), ('IN', 6000), ('OUT', 5000), ('IN', 10000)])
    transition(live, create(live, 'AA', ids[:2], ['AA_PAID', 'AA_RECEIVED']), 'confirm')
    transition(live, create(live, 'AA', ids[2:], ['AA_PAID', 'AA_RECEIVED']), 'confirm')
    total = body(live[0].get('/paam/ledger/v1/summary'))['totals'][0]
    assert (total['income_value'], total['expense_value']) == (5000, 4000), total


def test_partial_refund_honors_confirmed_allocation(live):
    ids = facts(live, [('IN', 10000), ('OUT', 30000)])
    case = create(live, 'REFUND', ids, ['REFUND_RECEIVED', 'REFUND_EXPENSE'], [6000, 6000])
    transition(live, case, 'confirm')
    total = body(live[0].get('/paam/ledger/v1/summary'))['totals'][0]
    assert total['refund_offset_value'] == 6000, total


def test_tag_restore_conflict_is_actionable(live):
    ids = facts(live, [('OUT', 1000), ('IN', 1000)])
    view = body(live[0].post('/paam/tag/v1/view/create', json={'name': 'Category', 'system_name': 'category'}))
    body(live[0].post(f'/paam/tag/v1/tag/create/{view["id"]}', json={'name': 'Food', 'system_name': 'food'}))
    entry = entries(live)[0]
    body(live[0].put(f'/paam/tag/v1/assignment/set/{entry["id"]}', json={
        'tag_state': {'category': 'food'}, 'expected_projection_version': entry['projection_version'],
        'idempotency_key': uuid4().hex}))
    body(live[0].put(f'/paam/tag/v1/view/status/{view["id"]}', json={'status': 'ARCHIVED'}))
    transition(live, create(live, 'TRANSFER', ids, ['TRANSFER_OUT', 'TRANSFER_IN']), 'confirm')
    response = live[0].put(f'/paam/tag/v1/view/status/{view["id"]}', json={'status': 'ACTIVE'})
    assert response.status_code in (200, 409, 422), f'Restore must succeed or explain the conflict, got {response.status_code}: {response.text}'
    if response.status_code != 200:
        assert response.json().get('detail')


def test_older_pending_review_is_reachable(live, page):
    with live[1]() as db:
        pending = ReviewCase(review_type='FACT_CONFLICT', status='PENDING', title='Old unresolved conflict')
        db.add(pending)
        db.flush()
        pending_id = pending.id
        db.add_all([ReviewCase(review_type='TAG', status='CONFIRMED', title=f'New tag review {i}') for i in range(200)])
        db.commit()
    page.locator('nav [data-page="reviews"]').click()
    page.locator('[data-form="review-filter"] [name="status"]').select_option('PENDING')
    page.locator('[data-form="review-filter"]').evaluate('(form) => form.requestSubmit()')
    expect(page.locator('[data-action="review-detail"]')).to_have_count(1)
    assert page.locator(f'[data-action="review-detail"][data-id="{pending_id}"]').count() == 1, 'Old pending conflict is hidden behind a fixed 200-case limit with no paging or filter'


def test_financial_review_ui_lifecycle_updates_ledger(live, page):
    ids = facts(live, [('OUT', 1000)])
    page.locator('nav [data-page="reviews"]').click()
    page.locator('[data-action="new-review"]').click()
    page.locator('[name="lines"]').fill(f'{ids[0]}:AA_PAID')
    page.locator('[data-form="new-review"] button.primary').click()
    expect(page.locator('[data-action="review-detail"]')).to_have_count(1)
    assert entries(live)[0]['ledger_type'] == 'EXPENSE'
    for action, expected_type in [('confirm', 'AA'), ('revoke', 'EXPENSE'), ('restore', 'AA')]:
        page.locator('[data-action="review-detail"]').click()
        page.locator(f'[data-action="review-transition"][data-kind="{action}"]').click()
        expect(page.locator('dialog')).to_have_count(0)
        expect(page.locator('[data-action="review-detail"]')).to_have_count(1)
        assert entries(live)[0]['ledger_type'] == expected_type
        assert body(live[0].get('/paam/ledger/v1/summary'))['totals'][0]['expense_value'] == 1000
