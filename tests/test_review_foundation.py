"""Business invariants, including conflicts that a happy-path UI misses."""
import json
import base64
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import database, main
from app.database import Base, Bill, CandidateActionLog, ImportRowIssue, LedgerOrigin, ReviewCandidate, ReviewMatterRevision, ImportBatch, ImportArtifact


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'foundation.db'}", connect_args={"check_same_thread": False, "timeout": .2})
    sessions = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    with TestClient(main.app) as client:
        yield client, sessions
    engine.dispose()


def bill(client, name, amount, minute=0, account="本人银行卡", day="2026-09-01"):
    response = client.post('/api/bills', json={"occurred_at": f"{day}T10:{minute:02d}:00", "merchant": name, "amount": amount, "account_name": account})
    assert response.status_code == 201, response.text
    return response.json()['id']


def line(bill_id, amount, role, party=""):
    return dict(bill_id=bill_id, amount=amount, role=role, party=party)


def matter(lines, key="matter-1", **kwargs):
    return {"title": "聚餐 AA", "scenarios": ["AA", "代付"], "lines": lines, "idempotency_key": key, "reason": "核对原始账单后确认", **kwargs}


def current_bill(client, bill_id):
    return next(b for b in client.get('/api/bills').json() if b['id'] == bill_id)


def test_aa_partial_payment_rounding_edit_and_immutable_revisions(ledger):
    client, sessions = ledger
    expense = bill(client, '聚餐', -300)
    repayment = bill(client, '朋友回款', 199.99, 20)
    rows = [line(expense, 100, 'expense'), line(expense, 200, 'receivable', '小王'), line(repayment, 199.99, 'repayment_received', '小王')]
    payload = matter(rows)
    created = client.post('/api/review-matters', json=payload)
    assert created.status_code == 201, created.text
    m = created.json()
    assert m['balances'][0]['amount_cents'] == 1
    assert client.post('/api/review-matters', json=payload).json()['id'] == m['id']
    summary = client.get('/api/dashboard').json()
    assert (summary['income'], summary['spending'], summary['cash_net']) == (0, -100, -100.01)
    first_snapshot = m['history'][0]['snapshot']
    # Explicitly assume the missing cent: do not silently swallow it as tolerance.
    rows[0]['amount'], rows[1]['amount'] = 100.01, 199.99
    updated = client.put(f"/api/review-matters/{m['id']}", json=matter(rows, "fix-cent", expected_version=1, reason="本人承担一分钱"))
    assert updated.status_code == 200, updated.text
    assert updated.json()['balances'][0]['amount_cents'] == 0
    assert updated.json()['history'][0]['snapshot'] == first_snapshot
    assert client.get('/api/dashboard').json()['spending'] == -100.01
    with sessions() as db:
        assert len(db.scalars(select(ReviewMatterRevision)).all()) == 2
    stale = client.put(f"/api/review-matters/{m['id']}", json=matter(rows, "stale", expected_version=1))
    assert stale.status_code == 409


def test_one_to_many_transfer_replacement_fee_and_undo(ledger):
    client, _ = ledger
    a = bill(client, '转出', -101)
    b = bill(client, '到账一', 60, 1, '本人余额')
    c = bill(client, '错误到账', 40, 2, '本人另一银行')
    d = bill(client, '正确到账', 40, 30, '本人另一银行')
    rows = [line(a, 100, 'transfer'), line(a, 1, 'expense'), line(b, 60, 'transfer'), line(c, 40, 'transfer')]
    created = client.post('/api/review-matters', json=matter(rows, own_accounts_confirmed=True))
    assert created.status_code == 201, created.text
    m = created.json()
    rows[-1]['bill_id'] = d
    updated = client.put(f"/api/review-matters/{m['id']}", json=matter(rows, "replace-c", expected_version=1, own_accounts_confirmed=True))
    assert updated.status_code == 200
    assert updated.json()['lines'][-1]['bill_id'] == d
    assert updated.json()['history'][0]['snapshot']['lines'][-1]['bill_id'] == c
    # The unrelated inflow C is once again ordinary; no facts were deleted.
    summary = client.get('/api/dashboard').json()
    assert summary['spending'] == -1 and summary['income'] == 40
    undo = client.post(f"/api/review-matters/{m['id']}/undo", json={"expected_version": 2, "idempotency_key": "undo", "reason": "重新核对"})
    assert undo.status_code == 200
    assert len(client.get('/api/bills').json()) == 4
    assert client.get('/api/dashboard').json()['spending'] == -101


def test_overallocation_invalid_settlement_and_partial_explanation(ledger):
    client, _ = ledger
    a, b = bill(client, '付款', -100), bill(client, '回款', 100, 20)
    assert client.post('/api/review-matters', json=matter([line(a, 60, 'expense')])).status_code == 201
    assert client.get('/api/dashboard').json()['unresolved_amount'] == 40
    assert client.post('/api/review-matters', json=matter([line(a, 41, 'expense')], "over")).status_code == 409
    assert client.post('/api/review-matters', json=matter([line(b, 1, 'repayment_received', '未知欠款')], "no-debt")).status_code == 422
    assert client.post('/api/review-matters', json=matter([line(a, 40, 'expense'), line(b, 100, 'expense')], "atomic-invalid")).status_code == 422
    assert len(client.get('/api/review-matters').json()) == 1


def test_two_independent_writes_cannot_occupy_same_money(ledger):
    client, sessions = ledger
    a = bill(client, '可用一百', -100)
    payloads = [matter([line(a, 70, 'expense')], f"writer-{i}") for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda p: client.post('/api/review-matters', json=p), payloads))
    assert sorted(r.status_code for r in responses) == [201, 409]
    with sessions() as db:
        assert len(db.scalars(select(ReviewMatterRevision)).all()) == 1
    assert client.get('/api/dashboard').json()['spending'] == -70


def test_refund_is_never_income_after_partial_allocation_or_undo(ledger):
    client, _ = ledger
    expense = bill(client, '上月购物', -300, day='2026-08-01')
    refund = bill(client, '本月退款', 100)
    first = client.post('/api/refund-allocations', json={"refund_bill_id": refund, "expense_bill_id": expense, "amount": 60, "idempotency_key": "sixty"})
    assert first.status_code == 201
    month = client.get('/api/dashboard?date_from=2026-09-01').json()
    assert (month['income'], month['refund_offset'], month['unallocated_refund']) == (0, 60, 40)
    all_time = client.get('/api/dashboard').json()
    assert (all_time['net'], all_time['cash_net']) == (-240, -200)
    second = client.post('/api/refund-allocations', json={"refund_bill_id": refund, "expense_bill_id": expense, "amount": 40, "idempotency_key": "forty"})
    assert second.status_code == 201
    assert client.post(f"/api/refund-allocations/{first.json()['id']}/undo", json={}).status_code == 200
    summary = client.get('/api/dashboard').json()
    assert (summary['income'], summary['refund_offset'], summary['unallocated_refund']) == (0, 40, 60)
    assert client.post('/api/review-matters', json=matter([line(expense, 300, 'expense')])).status_code == 409
    drill = client.get('/api/ledger/drilldown?date_from=2026-09-01').json()
    assert drill['composition']['refund_offset'][0]['expense_bill_id'] == expense
    assert sum(row['refund_offset'] for row in drill['contributions']) == 40


def test_refund_nature_correction_survives_restart(ledger):
    client, _ = ledger
    expense, refund = bill(client, '购买', -300), bill(client, '流入', 100, 20)
    allocation = client.post('/api/refund-allocations', json={"refund_bill_id": refund, "expense_bill_id": expense, "amount": 60, "idempotency_key": "allocation"}).json()
    nature = client.get(f'/api/transactions/{refund}/nature').json()
    change = {"nature": "ordinary", "expected_audit_id": nature['audit_id'], "reason": "关联错误，恢复普通流入"}
    assert client.put(f'/api/transactions/{refund}/nature', json=change).status_code == 409
    client.post(f"/api/refund-allocations/{allocation['id']}/undo", json={})
    assert client.put(f'/api/transactions/{refund}/nature', json=change).status_code == 200
    database.init_db()
    assert client.get('/api/dashboard').json()['income'] == 100


def test_confirmed_duplicate_members_never_expand_on_read(ledger):
    client, sessions = ledger
    a, b = bill(client, '同额商户', -10), bill(client, '同额商户', -10, 1)
    candidate = client.get('/api/candidates').json()[0]
    assert client.post(f"/api/candidates/{candidate['id']}", json={"action": "resolve_duplicate", "retained_bill_id": a}).status_code == 200
    c = bill(client, '同额商户', -10, 2)
    for _ in range(2):
        records = client.get('/api/candidates').json()
        confirmed = next(r for r in records if r['id'] == candidate['id'])
        assert {r['id'] for r in confirmed['member_bills']} == {a, b}
        assert current_bill(client, c)['aggregate_excluded'] is False
    assert client.get('/api/dashboard').json()['spending'] == -20
    with sessions() as db:
        assert len(db.scalars(select(CandidateActionLog)).all()) == 1


def test_transfer_conflict_and_remaining_decision_rebuild(ledger):
    client, sessions = ledger
    a = bill(client, '出', -100)
    b = bill(client, '入一', 100, 1, '本人余额')
    c = bill(client, '入二', 100, 2, '本人另一个账户')
    candidates = client.get('/api/candidates').json()
    ab = next(x for x in candidates if {x['bill']['id'], x['related_bill']['id']} == {a, b})
    ac = next(x for x in candidates if {x['bill']['id'], x['related_bill']['id']} == {a, c})
    assert client.post(f"/api/candidates/{ab['id']}", json={"action": "confirm_personal_transfer"}).status_code == 200
    assert client.post(f"/api/candidates/{ac['id']}", json={"action": "confirm_personal_transfer"}).status_code == 409
    # Simulate a corrupt old database which previously allowed both decisions.
    with sessions() as db:
        other = db.get(ReviewCandidate, ac['id'])
        other.status, other.transfer_group_id = 'personal_transfer_grouped', 'legacy-other'
        db.get(Bill, c).aggregate_excluded = True
        db.commit()
    assert client.post(f"/api/candidates/{ab['id']}/undo").status_code == 200
    assert current_bill(client, a)['aggregate_excluded'] is True
    assert current_bill(client, b)['aggregate_excluded'] is False


def test_tags_suggestions_do_not_override_and_undo_does_not_lock(ledger):
    client, _ = ledger
    a = bill(client, '滴滴出行', -10)
    assert current_bill(client, a)['category'] == '未分类'
    first = client.put(f'/api/transactions/{a}/tag-state', json={"tag_state": {"category": "food"}})
    assert first.status_code == 200
    audit = next(x for x in client.get(f'/api/bills/{a}/tags').json() if not x['superseded'])
    assert client.post(f'/api/bills/{a}/tags', json={"strategy": "llm_suggestion", "confidence": 1}).json()['action'] == 'suggest'
    assert current_bill(client, a)['tag_state']['category'] == 'food'
    assert client.post(f"/api/bills/{a}/tags/{audit['id']}/undo", json={}).status_code == 200
    assert client.put(f'/api/transactions/{a}/tag-state', json={"tag_state": {"category": "shopping"}}).status_code == 200
    assert current_bill(client, a)['tag_state']['category'] == 'shopping'
    assert client.put(f'/api/transactions/{a}/tag-state', json={"tag_state": {"category": "food"}, "expected_audit_id": audit['id']}).status_code == 409


def test_bad_rows_are_preserved_warned_and_correctable(ledger):
    client, sessions = ledger
    csv = ('交易创建时间,交易对方,金额（元）,收/支,交易号\n'
           '2026-09-01 10:00:00,正常,10,支出,a\n'
           ',缺少时间,20,支出,b\n'
           '2026-09-01 11:00:00,缺少方向,30,,c\n'
           '2026-09-01 12:00:00,坏金额,1abc2,支出,d\n').encode()
    preview = client.post('/api/imports/alipay/preview?filename=dirty.csv', content=csv).json()
    assert preview['valid_count'] == 1 and [i['row_number'] for i in preview['issues']] == [3, 4, 5]
    with sessions() as db:
        assert db.scalar(select(ImportRowIssue.id)) is None
    result = client.post('/api/imports/alipay?filename=dirty.csv', content=csv)
    assert result.status_code == 201 and result.json()['issue_count'] == 3
    assert client.get('/api/dashboard').json()['spending'] == -10
    issue = next(i for i in client.get('/api/import-issues').json() if i['row_number'] == 3)
    payload = {"occurred_at": "2026-09-01T10:00:00", "merchant": "缺少时间", "amount": -20, "account_name": "本人银行卡", "reason": "根据平台详情补充日期"}
    resolved = client.post(f"/api/import-issues/{issue['id']}/resolve", json=payload)
    assert resolved.status_code == 200
    assert client.post(f"/api/import-issues/{issue['id']}/resolve", json=payload).status_code == 409
    with sessions() as db:
        origin = db.scalar(select(LedgerOrigin).where(LedgerOrigin.bill_id == resolved.json()['id']))
        assert origin.raw_payload == db.get(ImportRowIssue, issue['id']).raw_payload
        assert json.loads(origin.raw_payload)['交易创建时间'] == ''
    assert client.post('/api/imports/alipay?filename=renamed.csv', content=csv).status_code == 409
    assert client.get('/api/dashboard').json()['issue_count'] == 2


@pytest.mark.parametrize('value', [0.001, 1e20])
def test_invalid_money_is_rejected_without_rounding(ledger, value):
    client, _ = ledger
    assert client.post('/api/bills', json={"occurred_at": "2026-09-01T10:00:00", "merchant": "无效金额", "amount": value}).status_code == 422


def test_facts_cannot_be_deleted_even_when_manually_entered(ledger):
    client, _ = ledger
    a = bill(client, '手工事实', -12)
    assert client.delete(f'/api/bills/{a}').status_code == 409
    assert current_bill(client, a)['amount'] == -12


def test_conflicting_transfer_batch_rolls_back_every_action(ledger):
    client, sessions = ledger
    a = bill(client, '出账', -100)
    b = bill(client, '入账一', 100, 1, '本人余额')
    c = bill(client, '入账二', 100, 2, '本人其他账户')
    candidates = client.get('/api/candidates').json()
    ab = next(x for x in candidates if {x['bill']['id'], x['related_bill']['id']} == {a, b})
    ac = next(x for x in candidates if {x['bill']['id'], x['related_bill']['id']} == {a, c})
    result = client.post('/api/candidates/batch', json={'items': [{'candidate_id': row['id'], 'action': 'confirm_personal_transfer'} for row in (ab, ac)]})
    assert result.status_code == 409
    assert all(not row['aggregate_excluded'] for row in client.get('/api/bills').json())
    with sessions() as db:
        assert db.scalar(select(CandidateActionLog.id)) is None


def test_candidate_evidence_and_undo_require_the_seen_version(ledger):
    client, _ = ledger
    a, b = bill(client, '同商户', -10), bill(client, '同商户', -10, 1)
    candidate = client.get('/api/candidates').json()[0]
    c = bill(client, '同商户', -10, 2)
    stale = client.post(f"/api/candidates/{candidate['id']}", json={'action': 'resolve_duplicate', 'retained_bill_id': a, 'expected_member_ids': [a, b]})
    assert stale.status_code == 409
    confirmed = client.post(f"/api/candidates/{candidate['id']}", json={'action': 'resolve_duplicate', 'retained_bill_id': a, 'expected_member_ids': [a, b, c]}).json()
    assert client.post(f"/api/candidates/{candidate['id']}/undo?expected_action_id=0").status_code == 409
    assert client.post(f"/api/candidates/{candidate['id']}/undo?expected_action_id={confirmed['current_action_id']}").status_code == 200


def test_bulk_tag_stale_input_is_atomic(ledger):
    client, _ = ledger
    a, b = bill(client, '一', -1), bill(client, '二', -2, 20)
    prior = {row['id']: row['tag_revision_id'] for row in client.get('/api/bills').json()}
    client.put(f'/api/transactions/{b}/tag-state', json={'tag_state': {'category': 'food'}})
    result = client.put('/api/transactions/bulk-tag-state', json={'bill_ids': [a, b], 'tag_state': {'category': 'shopping'}, 'merge': True, 'expected_revisions': prior})
    assert result.status_code == 409
    assert current_bill(client, a)['tag_state']['category'] == 'unclassified'
    assert current_bill(client, b)['tag_state']['category'] == 'food'


def test_import_constraint_failure_rolls_back_one_file_and_continues(ledger, monkeypatch):
    client, sessions = ledger
    original = main._generate_candidates
    def fail_one_row(db, row):
        if row.merchant == '数据库故障':
            raise IntegrityError('fixture constraint', {}, Exception('fixture'))
        return original(db, row)
    monkeypatch.setattr(main, '_generate_candidates', fail_one_row)
    header = '交易创建时间,交易对方,金额（元）,收/支,交易号\n'
    def file(name, merchants):
        rows = ''.join(f'2026-09-01 10:00:00,{merchant},10,支出,{name}-{i}\n' for i, merchant in enumerate(merchants))
        return {'filename': name + '.csv', 'content_base64': base64.b64encode((header + rows).encode()).decode()}
    result = client.post('/api/imports/alipay/batch', json={'files': [file('before', ['前文件']), file('bad', ['不应留下', '数据库故障']), file('after', ['后文件'])]})
    assert result.status_code == 201
    assert [row['status'] for row in result.json()['files']] == ['imported', 'error', 'imported']
    with sessions() as db:
        assert {b.merchant for b in db.scalars(select(Bill)).all()} == {'前文件', '后文件'}
        assert len(db.scalars(select(ImportBatch)).all()) == len(db.scalars(select(ImportArtifact)).all()) == 2


def test_dismissed_issue_can_be_reopened_without_losing_history(ledger):
    client, _ = ledger
    csv = '交易创建时间,交易对方,金额（元）,收/支,交易号\n,失败记录,20,支出,x\n'.encode()
    client.post('/api/imports/alipay?filename=failed.csv', content=csv)
    issue = client.get('/api/import-issues').json()[0]
    assert client.post(f"/api/import-issues/{issue['id']}/dismiss", json={'reason': '不是实际扣款'}).status_code == 200
    assert client.get('/api/dashboard').json()['issue_count'] == 0
    assert client.post(f"/api/import-issues/{issue['id']}/reopen", json={'reason': '需要重新核验'}).status_code == 200
    history = client.get('/api/import-issues').json()[0]
    assert history['raw_fields'] == issue['raw_fields']
    assert [row['action'] for row in history['history']] == ['dismiss', 'reopen']
    assert client.get('/api/dashboard').json()['issue_count'] == 1


def test_currency_cannot_be_silently_ignored(ledger):
    client, _ = ledger
    response = client.post('/api/bills', json={'occurred_at': '2026-09-01T10:00:00', 'merchant': '美元', 'amount': 100, 'currency': 'USD'})
    assert response.status_code == 422
