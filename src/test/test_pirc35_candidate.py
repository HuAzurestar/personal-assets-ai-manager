from datetime import datetime, timezone
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, update, event
from backend.core import target_database
from backend.entity import TransactionFact, LedgerAccountParty, LedgerAccount, LedgerAccountRef, ReviewCase, ReviewAllocation, LedgerEntry
from backend.mapper.review_command_mapper import ReviewCommandMapper
from backend.target_main import app


@pytest.fixture
def client():
    with TestClient(app) as client:
        with target_database.SessionLocal() as db:
            db.add(LedgerAccountParty(id=1, name="Mock person"))
            db.add(LedgerAccount(id=1, party_id=1, name="Mock group"))
            db.add_all([LedgerAccountRef(id=1, account_id=1), LedgerAccountRef(id=2, account_id=0)])
            db.add_all([TransactionFact(id=i, fact_key=f"mock-{i}", cash_direction=2, amount=1000,
                currency_code="CNY", account_code="IMMUTABLE SOURCE", summary="No match" if i == 1 else "Target",
                counterparty_name="Mock counterpart", occurred_time=datetime(2024, 1, i, tzinfo=timezone.utc)) for i in range(1, 4)])
            db.flush()
            ReviewCommandMapper(db).create_initial_defaults([1, 2, 3], account_refs={1: 1, 2: 2})
            db.commit()
        yield client


def page(client, **parameters):
    response = client.get("/paam/ledger/v1/candidate/list", params=parameters)
    assert response.status_code == 200, response.text
    return response.json()["body"]


def publish(client, **intent):
    preview = client.post('/paam/ledger/v1/review/preview', json=intent)
    assert preview.status_code == 200, preview.text
    plan = preview.json()['body']
    assert not plan['blocking_issues'], plan
    result = client.post('/paam/ledger/v1/review/command', json=intent | dict(
        expected_reviews=plan['expected_reviews'], preview_digest=plan['preview_digest']))
    assert result.status_code == 200, result.text
    return result.json()['body']


def test_current_review_summary_is_whole_group_masked_and_not_original_default(client):
    initial = page(client, filter='{"key":"id","op":"=","val":1}')['items'][0]
    assert initial['current_reviews'][0]['id'] == initial['default_review']['review_id']
    assert initial['current_reviews'][0]['member_count'] == 1
    result = publish(client, new_reviews=[dict(case_code='NORMAL', title='Mock 1234567890123456 owner@example.invalid',
        parameters=dict(transaction_ids=[1, 2]))])
    rid = result['created_reviews'][0]['id']
    item = page(client, page_size=1, filter='{"key":"id","op":"=","val":1}')['items'][0]
    assert item['default_review'] == initial['default_review']
    assert len(item['current_reviews']) == 1
    current = item['current_reviews'][0]
    assert current['id'] == rid and current['type'] == 'OTHER_MANUAL' and current['status'] == 'CONFIRMED'
    assert current['member_count'] == 2 and current['allocated_cash_amount'] == 1000
    assert '1234567890123456' not in current['title'] and 'owner@example.invalid' not in current['title']
    assert set(current) == {'id','title','type','status','created_time','updated_time','member_count','allocated_cash_amount'}
    publish(client, deactivate_review_ids=[rid])
    restored = page(client, filter='{"key":"id","op":"=","val":1}')['items'][0]
    assert restored['current_reviews'][0]['id'] == initial['default_review']['review_id']


def test_current_review_summary_keeps_all_legacy_active_splits_and_search_uses_same_projection(client):
    # Existing partial groups are a read compatibility fixture, not a new
    # permitted publication. New Review writes still require full coverage.
    with target_database.SessionLocal() as db:
        fact = db.get(TransactionFact,2)
        db.get(ReviewCase,2).status = 1
        for rid, title, amount in [(4,'Mock first split',400),(5,'Mock second split',600)]:
            db.add(ReviewCase(id=rid,behavior_type=4,status=0,title=title))
            db.add(LedgerEntry(id=rid,entry_type=0,entry_direction=fact.cash_direction,amount=amount,
                currency_code=fact.currency_code,account_ref_id=2,account_code=fact.account_code,occurred_time=fact.occurred_time))
            db.add(ReviewAllocation(id=rid,review_id=rid,transaction_id=2,ledger_id=rid,amount=amount,currency_code='CNY'))
        db.commit()
    item = page(client, filter='{"key":"id","op":"=","val":2}')['items'][0]
    reviews = item['current_reviews']
    assert {row['id'] for row in reviews} == {4,5}
    assert [row['allocated_cash_amount'] for row in reviews] == [400, 600]
    assert all(row['member_count'] == 1 for row in reviews)
    searched = client.get('/paam/ledger/v1/candidate/search', params=dict(page_size=1,
        filter='{"key":"id","op":"=","val":2}', query='[{"key":"summary","word":"Target"}]'))
    assert searched.status_code == 200, searched.text
    assert searched.json()['body']['items'] == [item]


def test_review_fact_members_are_distinct_complete_paged_and_readable_after_revoke(client):
    result = publish(client, new_reviews=[dict(case_code='NORMAL', title='Mock whole group', parameters=dict(
        allocations=[dict(transaction_id=fid,economic_type='TRANSACTION',
            cash_amount=amount,account_ref_id=0) for fid, amount in [(1,400),(1,600),(2,1000)]]))])
    rid = result['created_reviews'][0]['id']
    url = f'/paam/ledger/v1/review/{rid}/fact/list'
    one = client.get(url, params=dict(page_size=1)).json()['body']
    assert set(one) == {'items','total','page_index','page_size'} and one['total'] == 2
    assert one['items'][0]['transaction_id'] == 1 and one['items'][0]['current_reviews'][0]['member_count'] == 2
    two = client.get(url, params=dict(page_index=2,page_size=1)).json()['body']
    assert two['total'] == 2 and two['items'][0]['transaction_id'] == 2
    publish(client, deactivate_review_ids=[rid])
    historical = client.get(url).json()['body']
    assert {row['transaction_id'] for row in historical['items']} == {1,2}
    assert all(row['current_reviews'][0]['type'] == 'NORMAL_TRANSACTION' for row in historical['items'])
    assert client.get('/paam/ledger/v1/review/999/fact/list').status_code == 404
    assert client.get(url, params=dict(query='[{"key":"summary","word":"Mock"}]')).status_code == 422
    assert client.get(f'/paam/ledger/v1/review/{2**63}/fact/list').status_code == 422


def test_current_review_projection_and_member_queries_are_page_bounded_not_per_fact(client):
    with target_database.SessionLocal() as db:
        ids = list(range(4, 101))
        db.add_all([TransactionFact(id=i,fact_key=f'mock-{i}',cash_direction=2,amount=1000,currency_code='CNY',
            account_code='',summary='Mock extra',occurred_time=datetime(2024,1,1,tzinfo=timezone.utc)) for i in ids])
        db.flush()
        ReviewCommandMapper(db).create_initial_defaults(ids)
        db.commit()
    # Expanded publication includes original defaults, even inactive ones.
    # A new 100-member group therefore exceeds the 100-Review write budget.
    # Preserve that boundary; read compatibility must still handle a valid
    # existing 100-member group rather than silently reducing the read test.
    intent = dict(new_reviews=[dict(case_code='NORMAL',title='Mock too many groups',
        parameters=dict(transaction_ids=list(range(1,101))))])
    rejected = client.post('/paam/ledger/v1/review/preview',json=intent).json()['body']
    assert rejected['blocking_issues'][0]['code'] == 'REVIEW_CHANGE_LIMIT'
    rid = 101
    with target_database.SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ReviewCase)) == 100
        facts = db.scalars(select(TransactionFact).order_by(TransactionFact.id)).all()
        db.execute(update(ReviewCase).values(status=1))
        db.add(ReviewCase(id=rid,behavior_type=4,status=0,title='Mock legacy 100 member group'))
        for fact in facts:
            lid = 100 + fact.id
            db.add(LedgerEntry(id=lid,entry_type=0,entry_direction=fact.cash_direction,amount=fact.amount,
                currency_code=fact.currency_code,account_ref_id={1:1,2:2}.get(fact.id,0),
                account_code=fact.account_code,occurred_time=fact.occurred_time))
            db.add(ReviewAllocation(id=lid,review_id=rid,transaction_id=fact.id,ledger_id=lid,
                amount=fact.amount,currency_code=fact.currency_code))
        db.commit()
    statements = []
    def count(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(target_database.engine,'before_cursor_execute',count)
    try:
        counts = {}
        for path in ['/candidate/list', '/candidate/search', f'/review/{rid}/fact/list']:
            for size in [1,100]:
                statements.clear()
                response = client.get('/paam/ledger/v1' + path, params=dict(page_size=size))
                assert response.status_code == 200, response.text
                assert all('current_reviews' in row for row in response.json()['body']['items'])
                assert len(response.json()['body']['items']) == size
                assert all(row['current_reviews'][0]['member_count'] == 100
                    for row in response.json()['body']['items'])
                counts[path,size] = len(statements)
            assert counts[path,1] == counts[path,100]
    finally:
        event.remove(target_database.engine,'before_cursor_execute',count)


def test_current_review_huge_title_refuses_whole_page_and_member_scope_cannot_hide_orphans(client):
    with target_database.SessionLocal() as db:
        db.get(ReviewCase,1).title = 'Mock ' * 450000
        db.commit()
    response = client.get('/paam/ledger/v1/candidate/list',params=dict(filter='{"key":"id","op":"=","val":1}'))
    assert response.status_code == 413 and response.json()['body']['code'] == 'DETAIL_LIMIT'
    with target_database.SessionLocal() as db:
        db.execute(update(ReviewAllocation).where(ReviewAllocation.transaction_id == 3).values(ledger_id=987654))
        db.commit()
    response = client.get('/paam/ledger/v1/review/2/fact/list')
    assert response.status_code == 409 and response.json()['body']['code'] == 'RELATION_BROKEN'


def test_current_review_group_limit_refuses_instead_of_hiding_summaries(client,monkeypatch):
    from backend.mapper import candidate_mapper
    monkeypatch.setattr(candidate_mapper,'MAX_CURRENT_REVIEW_SUMMARIES',2)
    response = client.get('/paam/ledger/v1/candidate/list',params=dict(page_size=3))
    assert response.status_code == 413 and response.json()['body']['code'] == 'DETAIL_LIMIT'
    assert len(page(client,page_size=1)['items'][0]['current_reviews']) == 1


def test_current_review_and_members_share_budget_and_openapi_contract(client,monkeypatch):
    schema = client.get('/openapi.json').json()
    assert '/paam/ledger/v1/review/{review_id}/fact/list' in schema['paths']
    candidate = schema['components']['schemas']['CandidatePO']
    assert 'current_reviews' in candidate['required']
    summary = schema['components']['schemas']['CurrentReviewPO']
    assert {'member_count','allocated_cash_amount','title','status'} <= set(summary['required'])
    from backend.service import candidate_service
    from backend.mapper.bounded_query_mapper import query_budget
    monkeypatch.setattr(candidate_service,'query_budget',lambda db: query_budget(db,seconds=-1))
    for path in ['/candidate/list','/candidate/search','/review/1/fact/list']:
        response = client.get('/paam/ledger/v1' + path)
        assert response.status_code == 503 and response.json()['body']['code'] == 'QUERY_BUSY'


def test_all_facts_still_candidates_after_manual_review_and_unique_default_unchanged(client):
    initial = page(client, page_size=1)
    assert initial["total"] == 3 and set(initial) == {"items", "total", "page_index", "page_size"}
    intent = dict(new_reviews=[dict(case_code="NORMAL", parameters=dict(transaction_ids=[1, 2]))])
    preview = client.post("/paam/ledger/v1/review/preview", json=intent).json()["body"]
    response = client.post("/paam/ledger/v1/review/command", json=intent | dict(
        expected_reviews=preview["expected_reviews"], preview_digest=preview["preview_digest"]))
    assert response.status_code == 200, response.text
    result = page(client)
    assert result["total"] == 3
    for item in result["items"]:
        assert item["default_review"]["review_id"] == item["transaction_id"]
        assert item["coverage"]["state"] == "FULL"
        assert item["coverage"]["allocated_cash_amount"] == item["cash_amount"] == 1000
        assert item["cash_direction"] == "OUT" and "account_code" not in item


@pytest.mark.parametrize("dimension,value,expected", [("account_ref_id", 1, {1}), ("account_ref_id", 0, {3}),
    ("account_id", 1, {1}), ("account_id", 0, {2}), ("party_id", 1, {1}), ("party_id", 99, set())])
def test_current_ownership_filter_known_unassigned_distinct_from_unknown(client, dimension, value, expected):
    items = page(client, filter=json.dumps(dict(key=dimension, op="=", val=value)))["items"]
    assert {item["transaction_id"] for item in items} == expected
    with target_database.SessionLocal() as db:
        db.execute(update(LedgerAccountRef).where(LedgerAccountRef.id == 2).values(account_id=1, status="CLOSED"))
        db.commit()
    assert {item["transaction_id"] for item in page(client, filter='{"key":"party_id","op":"=","val":1}')["items"]} == {1, 2}


def test_candidate_search_scans_once_per_batch_and_preserves_empty_hit_cursor(client):
    params = dict(page_size=1, sorter='[{"key":"id","direction":"asc"}]', query='[{"key":"summary","word":"Target"}]')
    first = client.get("/paam/ledger/v1/candidate/search", params=params).json()["body"]
    assert first["items"] == [] and first["has_more"] and first["total"] is None and first["scanned_count"] == 1
    second = client.get("/paam/ledger/v1/candidate/search", params=params | dict(cursor=first["next_cursor"])).json()["body"]
    assert second["items"][0]["transaction_id"] == 2
    assert second["items"][0]["coverage"]["allocated_cash_amount"] == 1000


def test_candidate_missing_original_default_is_disclosed_never_repaired(client):
    with target_database.SessionLocal() as db:
        db.add(TransactionFact(id=4, fact_key="missing-default", cash_direction=2, amount=1000, currency_code="CNY",
            account_code="", occurred_time=datetime(2024, 1, 1, tzinfo=timezone.utc)))
        db.commit()
    item = page(client, filter='{"key":"id","op":"=","val":4}')["items"][0]
    assert item["default_review"] is None
    assert item['current_reviews'] == []
    assert item["coverage"]["default_identity_state"] == "MISSING" and item["coverage"]["state"] == "UNRESOLVED"
    with target_database.SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ReviewCase)) == 3


def test_candidate_time_between_is_left_closed_right_open_and_no_account_sort(client):
    result = page(client, filter=json.dumps(dict(key="occurred_time", op="between",
        val=dict(start="2024-01-01T00:00:00Z", end="2024-01-03T00:00:00Z"))))
    assert {item["transaction_id"] for item in result["items"]} == {1, 2}
    response = client.get("/paam/ledger/v1/candidate/list", params=dict(sorter='[{"key":"account_ref_id","direction":"asc"}]'))
    assert response.status_code == 422


def test_candidate_exact_po_masked_search_and_legacy_url_share_guard(client):
    with target_database.SessionLocal() as db:
        db.get(TransactionFact,2).summary = 'Mock 1234567890123456 bill@example.invalid'
        db.commit()
    canonical = page(client)
    expected = {'transaction_id','default_review','occurred_time','cash_direction','cash_amount',
        'cash_currency_code','summary','coverage','account_ref_id','current_reviews'}
    assert all(set(item) == expected for item in canonical['items'])
    encoded = json.dumps(canonical)
    assert '1234567890123456' not in encoded and 'bill@example.invalid' not in encoded
    alias = client.get('/paam/ledger/v1/review_candidate/list')
    assert alias.status_code == 200 and alias.json()['body'] == canonical
    private = client.get('/paam/ledger/v1/candidate/search',params=dict(query=json.dumps([dict(key='summary',word='1234567890123456')])))
    assert private.status_code == 200 and private.json()['body']['items'] == []
    assert client.get('/paam/ledger/v1/candidate/list',params=dict(filter=json.dumps(dict(key='id',op='=',val=2**63)))).status_code == 422
