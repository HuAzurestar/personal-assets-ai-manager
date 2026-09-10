"""Regression checks for the endpoints used by the rebuilt workbench."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, Bill, ReviewCandidate
from app.main import app, get_db


@pytest.fixture
def ui_client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ui.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    def dependency():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    try:
        with TestClient(app) as client:
            yield client, sessions
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_original_scope_does_not_change_effective_summary(ui_client):
    client, sessions = ui_client
    with sessions() as db:
        db.add_all([
            Bill(occurred_at=datetime(2026, 9, 8), merchant="有效", amount=-10, note="", category="未分类", tags=""),
            Bill(occurred_at=datetime(2026, 9, 8), merchant="已排除", amount=-20, note="", category="未分类", tags="", aggregate_excluded=True, transfer_group_id="group"),
        ])
        db.commit()
    assert client.get('/api/transactions').json()['total'] == 1
    assert client.get('/api/transactions?scope=all').json()['total'] == 2
    excluded = client.get('/api/transactions?scope=excluded').json()
    assert excluded['total'] == 1 and excluded['items'][0]['aggregate_excluded']
    assert client.get('/api/transactions?scope=all&direction=transfer').json()['total'] == 1
    assert client.get('/api/transactions?scope=invalid').status_code == 422
    assert client.get('/api/dashboard').json()['spending'] == -10


def test_bulk_merge_preserves_other_dimensions_and_is_atomic(ui_client):
    client, _ = ui_client
    view1 = client.post('/api/tag-views', json={'name': '项目', 'system_name': 'project'}).json()
    view2 = client.post('/api/tag-views', json={'name': '用途', 'system_name': 'purpose'}).json()
    client.post(f"/api/tag-views/{view1['id']}/tags", json={'name': '旅行', 'system_name': 'travel'})
    client.post(f"/api/tag-views/{view2['id']}/tags", json={'name': '日常', 'system_name': 'daily'})
    ids = [client.post('/api/bills', json={'occurred_at': '2026-09-08T10:00:00', 'merchant': f'商户{i}', 'amount': -10}).json()['id'] for i in range(2)]
    client.put(f'/api/transactions/{ids[0]}/tag-state', json={'tag_state': {'purpose': 'daily'}})
    result = client.put('/api/transactions/bulk-tag-state', json={'bill_ids': ids, 'tag_state': {'project': 'travel'}, 'merge': True})
    assert result.status_code == 200
    bills = {b['id']: b for b in client.get('/api/transactions').json()['items']}
    assert bills[ids[0]]['tag_state']['purpose'] == 'daily'
    assert bills[ids[1]]['tag_state'].get('purpose', 'unclassified') == 'unclassified'
    assert all(b['tag_state']['project'] == 'travel' for b in bills.values())
    failed = client.put('/api/transactions/bulk-tag-state', json={'bill_ids': [ids[0], 999999], 'tag_state': {'project': 'unclassified'}, 'merge': True})
    assert failed.status_code == 404
    assert client.get('/api/transactions?tag=project:travel').json()['total'] == 2


def test_new_workbench_is_the_served_entrypoint(ui_client):
    client, _ = ui_client
    html = client.get('/').text
    assert '/static/ledger.js' in html and '/static/ledger.css' in html
    assert 'workspace-next.js' not in html
    assert client.get('/static/ledger.js').status_code == 200
    assert client.get('/static/style-cards.html').status_code == 200


def test_confirmed_transfer_filter_includes_personal_and_third_party(ui_client):
    client, _ = ui_client
    for hour, amount, decision in [(10, 100, 'confirm_personal_transfer'), (11, 200, 'confirm_third_party_transfer')]:
        first = client.post('/api/bills', json={'occurred_at': f'2026-09-08T{hour}:00:00', 'merchant': f'转账{hour}', 'amount': -amount, 'account_name': '银行卡'}).json()
        client.post('/api/bills', json={'occurred_at': f'2026-09-08T{hour}:01:00', 'merchant': f'转账{hour}', 'amount': amount, 'account_name': '余额'})
        candidates = client.get('/api/candidates/page?status=needs_review').json()['items']
        candidate = next(c for c in candidates if first['id'] in {c['bill']['id'], c['related_bill']['id']})
        assert client.post(f"/api/candidates/{candidate['id']}", json={'action': decision}).status_code == 200
    result = client.get('/api/candidates/page?status=transfer_grouped').json()
    assert result['total'] == 2
    assert {c['status'] for c in result['items']} == {'personal_transfer_grouped', 'third_party_transfer_grouped'}
    assert client.get('/api/candidates/page?status=needs_review').json()['total'] == 0
