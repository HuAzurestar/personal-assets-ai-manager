"""Fictional SQLite Fact projection, bounded evidence and canonical relations."""
import json
import pytest
from sqlalchemy import event, update, text
from backend.entity import TransactionFact, TransactionImportRow, ReviewCase, ReviewAllocation
from test_transaction_fact_api import transaction_fact_api, _seed
from test_pirc35_flow_read import body, PO as FLOW_PO

BASE = '/paam/ledger/v1'
FACT_PO = {'id','occurred_time','cash_direction','amount','currency_code','account_code',
    'counterparty_name','counterparty_account_ref','summary','created_time','updated_time'}
ALLOC_PO = {'id','review_id','transaction_id','ledger_id','cash_amount','cash_currency_code','created_time','updated_time'}


def test_fact_public_projection_masks_source_text_and_preserves_immutable_db(transaction_fact_api):
    client, sessions = transaction_fact_api
    fid = _seed(sessions)[0]
    original = 'Mock 1234567890123456 bill@example.invalid'
    with sessions() as db:
        db.execute(update(TransactionFact).where(TransactionFact.id == fid).values(summary=original,
            counterparty_name=original,account_code='1234567890123456',counterparty_account_ref='1234567890123456'))
        db.execute(update(TransactionImportRow).where(TransactionImportRow.transaction_fact_id == fid).values(
            raw_payload='{"mock_source":"private"}',source_reference='1234567890123456'))
        db.commit()
    page = body(client.get(f'{BASE}/transaction_fact/list'))
    assert all(set(item) == FACT_PO for item in page['items'])
    detail = body(client.get(f'{BASE}/transaction_fact/{fid}'))
    encoded = json.dumps(detail)
    assert '1234567890123456' not in encoded and 'bill@example.invalid' not in encoded
    assert 'raw_payload' not in encoded and 'private' not in encoded
    assert detail['transaction_fact']['cash_direction'] == 'OUT'
    assert set(detail['allocations'][0]) == ALLOC_PO
    assert set(detail['ledgers'][0]) == FLOW_PO
    assert detail['reviews'][0]['status'] == 'CONFIRMED'
    with sessions() as db:
        assert db.get(TransactionFact, fid).summary == original
        assert db.get(TransactionImportRow,1).raw_payload == '{"mock_source":"private"}'


def test_fact_count_page_queries_fixed_and_semantic_or_legacy_read_filter(transaction_fact_api):
    client,sessions = transaction_fact_api
    ids = _seed(sessions)
    statements = []
    def count(_conn,_cursor,statement,_params,_context,_many):
        if statement.lstrip().upper().startswith('SELECT'):
            statements.append(statement)
    engine = sessions.kw['bind']
    event.listen(engine,'before_cursor_execute',count)
    try:
        result = body(client.get(f'{BASE}/transaction_fact/list',params=dict(page_size=1)))
    finally:
        event.remove(engine,'before_cursor_execute',count)
    assert result['total'] == 2 and len(result['items']) == 1 and len(statements) == 3
    for direction in ('IN',1):
        page = body(client.get(f'{BASE}/transaction_fact/list',params=dict(filter=json.dumps(dict(key='cash_direction',op='=',val=direction)))))
        assert [row['id'] for row in page['items']] == [ids[1]] and page['items'][0]['cash_direction'] == 'IN'


@pytest.mark.parametrize('params', [dict(query='[{"key":"summary","word":"Coffee"}]'),
    dict(filter='{"key":"id","op":"=","val":true}'),dict(filter='{"key":"id","op":"=","val":9223372036854775808}'),
    dict(sorter='[{"key":"account_ref_id","direction":"asc"}]'),
    dict(sorter='[{"key":"amount","direction":"asc"},{"key":"amount","direction":"desc"}]'),
    dict(filter='{"key":"occurred_time","op":">=","val":"2026-09-01T00:00:00"}')])
def test_fact_invalid_queries_rejected(transaction_fact_api, params):
    client,_ = transaction_fact_api
    assert client.get(f'{BASE}/transaction_fact/list',params=params).status_code == 422


def test_fact_literal_masked_search_empty_batch_and_cursor_binding(transaction_fact_api):
    client,sessions = transaction_fact_api
    fid = _seed(sessions)[0]
    with sessions() as db:
        db.get(TransactionFact,fid).summary = 'Mock Cafe\u0301 literal %_*? 1234567890123456'
        db.commit()
    conditions = dict(page_size=1,sorter='[{"key":"id","direction":"desc"}]',query=json.dumps([dict(key='summary',word='CAFÉ literal %_*?')]))
    first = body(client.get(f'{BASE}/fact/search',params=conditions))
    assert first['items'] == [] and first['has_more'] and first['total'] is None and first['scanned_count'] == 1
    second = body(client.get(f'{BASE}/fact/search',params=conditions | dict(cursor=first['next_cursor'])))
    assert [row['id'] for row in second['items']] == [fid] and set(second['items'][0]) == FACT_PO
    final = body(client.get(f'{BASE}/fact/search',params=conditions | dict(cursor=second['next_cursor'])))
    assert final['items'] == [] and not final['has_more']
    private = body(client.get(f'{BASE}/fact/search',params=dict(query=json.dumps([dict(key='summary',word='1234567890123456')]))))
    assert private['items'] == []
    mismatch = client.get(f'{BASE}/fact/search',params=conditions | dict(page_size=2,cursor=first['next_cursor']))
    assert mismatch.status_code == 422


def test_fact_original_allocation_pages_preserve_revoked_reviews(transaction_fact_api):
    client,sessions = transaction_fact_api
    ids = _seed(sessions)
    with sessions() as db:
        db.get(ReviewCase,1).status = 1
        db.commit()
    page = body(client.get(f'{BASE}/fact/{ids[0]}/allocation/list',params=dict(page_size=1)))
    assert set(page) == {'items','total','page_index','page_size'} and page['total'] == 1
    item = page['items'][0]
    assert set(item) == {'allocation','review','ledger_entry'}
    assert set(item['allocation']) == ALLOC_PO and item['review']['status'] == 'REVOKED'
    assert set(item['ledger_entry']) == FLOW_PO
    empty = body(client.get(f'{BASE}/fact/{ids[0]}/allocation/list',params=dict(filter=json.dumps(dict(key='ledger_id',op='=',val=ids[1])))))
    assert empty['total'] == 0


def test_fact_source_orphan_fails_detail_and_source_page_without_repair(transaction_fact_api):
    client,sessions = transaction_fact_api
    fid = _seed(sessions)[0]
    with sessions() as db:
        db.get(TransactionImportRow,1).transaction_import_file_id = 999
        db.commit()
    for path in (f'/transaction_fact/{fid}',f'/fact/{fid}/source_row/list'):
        response = client.get(BASE + path)
        assert response.status_code == 409 and response.json()['body']['code'] == 'RELATION_BROKEN'
    with sessions() as db:
        assert db.get(TransactionImportRow,1).transaction_import_file_id == 999


def test_fact_relation_guard_runs_before_filter_and_large_evidence_is_paged(transaction_fact_api):
    client,sessions = transaction_fact_api
    fid = _seed(sessions)[0]
    with sessions() as db:
        db.execute(text("""WITH RECURSIVE seq(i) AS (VALUES(3) UNION ALL SELECT i+1 FROM seq WHERE i<4002)
            INSERT INTO transaction_import_row(transaction_fact_id,transaction_import_file_id,source_row_number,
              source_reference,raw_payload,raw_hash,row_status,issue_code,issue_message,created_time,updated_time)
            SELECT :fid,1,i,'mock-'||i,'{"fictional":"private"}','','1','','',
              '2026-09-01T00:00:00.000000Z','2026-09-01T00:00:00.000000Z' FROM seq"""),dict(fid=fid))
        db.commit()
    response = client.get(f'{BASE}/transaction_fact/{fid}')
    assert response.status_code == 413 and response.json()['body']['code'] == 'DETAIL_LIMIT'
    result = body(client.get(f'{BASE}/fact/{fid}/source_row/list',params=dict(page_index=2,page_size=100)))
    assert result['total'] == 4001 and len(result['items']) == 100
    assert all(row['transaction_id'] == fid and 'raw_payload' not in row for row in result['items'])
    with sessions() as db:
        db.get(ReviewAllocation,1).ledger_id = 999999
        db.commit()
    response = client.get(f'{BASE}/transaction_fact/list',params=dict(filter='{"key":"id","op":"=","val":999}'))
    assert response.status_code == 409 and response.json()['body']['code'] == 'RELATION_BROKEN'


def test_fact_signed_amount_sort_groups_currency_and_ownership_is_filter_only(transaction_fact_api):
    client,sessions = transaction_fact_api
    ids = _seed(sessions)
    result = body(client.get(f'{BASE}/transaction_fact/list',params=dict(sorter='[{"key":"signed_amount","direction":"desc"}]')))
    assert [row['currency_code'] for row in result['items']] == ['CNY','USD']
    unknown = body(client.get(f'{BASE}/transaction_fact/list',params=dict(filter='{"key":"account_ref_id","op":"=","val":0}')))
    assert {row['id'] for row in unknown['items']} == set(ids)
    unassigned = body(client.get(f'{BASE}/transaction_fact/list',params=dict(filter='{"key":"account_id","op":"=","val":0}')))
    assert unassigned['total'] == 0
