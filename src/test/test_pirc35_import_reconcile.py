"""Current-state observations only: no receipts, guessing or financial replay."""
from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select, update

from backend.entity import TransactionImportFile, TransactionImportRow, LedgerEntry
from backend.schema.import_source_read import SourceReconcileInput, SourceReconcilePO
from backend.service.import_source_service import ImportSourceService
from test_pirc35_import_service import service  # noqa: F401
from test_pirc35_import_api import client, BASE  # noqa: F401
from test_pirc35_import_batch import prepare, row, accept
from test_pirc35_import_confirm_preview import install, batch, manual_fact
from test_pirc35_import_duplicate import existing_pair, local_pair, confirm_pair, manifest
from test_pirc35_review_command import execute, normal


def payload(service, rows, choices=None):
    choices = choices or {}
    ids = {key[0] for key in rows}
    ids |= {value['target']['file_id'] for value in choices.values()
        if value.get('target', {}).get('kind') == 'ROW'}
    files = service.mapper.rows(TransactionImportFile, TransactionImportFile.id, ids)
    return dict(rows=[dict(file_id=key[0], source_row_number=key[1],
        **{field: choices[key][field] for field in ('resolution', 'target', 'decision')
            if field in choices.get(key, {})}) for key in rows],
        files=[dict(file_id=value['id'], sha256=value['sha256']) for value in files])


def observe(service, body):
    before = manifest(service)
    state = ImportSourceService(service.db).reconcile(SourceReconcileInput(**body))
    SourceReconcilePO(**state)
    assert manifest(service) == before
    assert state['current_state_only'] is True
    assert len(state['items']) == len(body['rows'])
    return state


def test_link_requires_exact_persisted_pointer_and_keeps_original_history(service):
    current, keys, ids = manual_fact(service)
    choices = service.store.get(current['token']).choices
    confirm_pair(service, current, keys, batch(service, current, keys))
    body = payload(service, keys, choices)
    state = observe(service, body)
    item = state['items'][0]
    assert state['fully_observed'] and item['state'] == 'EVIDENCE_LINKED'
    assert item['transaction_id'] == item['target_transaction_id'] == ids[0]
    assert len(state['facts']) == len(state['outputs']) == 1
    assert state['outputs'][0]['economic_type'] == 'TRANSACTION'
    body['rows'][0]['target']['transaction_id'] = ids[0] + 999
    state = observe(service, body)
    assert not state['fully_observed'] and state['items'][0]['state'] == 'UNRESOLVED'
    assert state['items'][0]['reason_codes'] == ['PAIR_TARGET_NOT_OBSERVED']


@pytest.mark.parametrize('local', [False, True])
def test_duplicate_observes_explicit_keeper_both_current_and_revoked_outputs(service, local):
    if local:
        rows, choices, anchor, source = local_pair(service, link=True)
    else:
        keeper, rows, choices = existing_pair(service)
        source = next(iter(rows))
    current = install(service, rows, choices)
    result = confirm_pair(service, current, list(rows), batch(service, current, list(rows)))
    actual = {(value['file_id'], value['source_row_number']): value['transaction_id']
        for value in result['processed_rows']}
    state = observe(service, payload(service, rows, choices))
    duplicate = next(value for value in state['items'] if value['resolution'] == 'DUPLICATE')
    assert state['fully_observed'] and duplicate['state'] == 'DUPLICATE_EXCLUDED'
    assert duplicate['transaction_id'] == actual[source]
    assert duplicate['target_transaction_id'] == (actual[anchor] if local else keeper)
    assert duplicate['reason_codes'] == ['KEEPER_LOCATED_FROM_CLIENT_CONTEXT']
    outputs = [value for value in state['outputs'] if value['transaction_id'] == actual[source]]
    assert {(value['review_status'], value['economic_type']) for value in outputs} == {
        ('REVOKED', 'TRANSACTION'), ('CONFIRMED', 'DUPLICATE')}
    assert len(state['facts']) == 2 and len(state['outputs']) == 3
    assert not any(field in str(state) for field in ('raw_payload', 'fact_key', 'account_code', 'source_identity'))


@pytest.mark.parametrize('change', ['missing_target', 'wrong_core', 'missing_hash', 'wrong_hash', 'missing_row_target'])
def test_lost_or_wrong_pair_context_never_unlocks_or_guesses(service, change):
    rows, choices, anchor, source = local_pair(service)
    current = install(service, rows, choices)
    confirm_pair(service, current, list(rows), batch(service, current, list(rows)))
    body = payload(service, [source], choices)
    intent = body['rows'][0]
    expected = 'SOURCE_FILE_IDENTITY_REQUIRED'
    if change == 'missing_target':
        intent.pop('target')
        expected = 'PAIR_CONTEXT_REQUIRED'
    elif change == 'wrong_core':
        additional = prepare(service.mapper, [row(reference='different', amount_minor=-123)], sha='c' * 64)
        id = accept(service.mapper, additional)['processed_rows'][0]['transaction_id']
        intent['target'] = dict(kind='FACT', transaction_id=id)
        expected = 'PAIR_TARGET_CORE_MISMATCH'
    elif change == 'missing_hash':
        body['files'] = [value for value in body['files'] if value['file_id'] != anchor[0]]
    elif change == 'wrong_hash':
        body['files'][0]['sha256'] = 'f' * 64
    else:
        intent['target']['source_row_number'] = 999
        expected = 'PAIR_TARGET_NOT_OBSERVED'
    state = observe(service, body)
    assert not state['fully_observed'] and state['items'][0]['state'] == 'UNRESOLVED'
    assert state['items'][0]['reason_codes'] == [expected]


def test_link_to_other_existing_real_fact_remains_unresolved(service):
    current, keys, _ = manual_fact(service)
    choices = service.store.get(current['token']).choices
    confirm_pair(service, current, keys, batch(service, current, keys))
    additional = prepare(service.mapper, [row(reference='other-A')], sha='c' * 64)
    other = accept(service.mapper, additional)['processed_rows'][0]['transaction_id']
    body = payload(service, keys, choices)
    body['rows'][0]['target']['transaction_id'] = other
    state = observe(service, body)
    assert not state['fully_observed']
    assert state['items'][0]['reason_codes'] == ['EVIDENCE_TARGET_CHANGED']


def test_later_legitimate_explanation_is_current_change_not_failed_import(service):
    keeper, rows, choices = existing_pair(service)
    current = install(service, rows, choices)
    result = confirm_pair(service, current, list(rows), batch(service, current, list(rows)))
    source_id = result['processed_rows'][0]['transaction_id']
    execute(service.db, new_reviews=[normal(source_id)])
    state = observe(service, payload(service, rows, choices))
    item = state['items'][0]
    assert state['fully_observed'] and item['state'] == 'CURRENT_STATE_CHANGED'
    assert item['target_transaction_id'] == keeper
    assert item['reason_codes'] == ['CURRENT_PAIR_EFFECT_CHANGED', 'KEEPER_LOCATED_FROM_CLIENT_CONTEXT']
    assert {value['economic_type'] for value in state['outputs']} == {'TRANSACTION', 'DUPLICATE'}
    assert len(state['outputs']) == 4


@pytest.mark.parametrize('status,state,observed', [(0, 'UNPROCESSED', False), (2, 'SKIPPED', True), (3, 'INVALID', True)])
def test_existing_nonaccepted_state_is_observation_not_original_receipt(service, status, state, observed):
    rows = prepare(service.mapper, [row()])
    accept(service.mapper, rows, {key: dict(decision='SKIP') for key in rows})
    service.db.execute(update(TransactionImportRow).values(row_status=status))
    service.db.commit()
    result = observe(service, payload(service, rows))
    assert result['items'][0]['state'] == state and result['fully_observed'] is observed
    assert result['items'][0]['transaction_id'] == 0 and result['facts'] == result['outputs'] == []


def test_missing_persisted_source_not_proof_of_no_submission(service):
    rows = prepare(service.mapper, [row()])
    result = observe(service, payload(service, rows))
    assert result['items'][0]['state'] == 'NOT_PERSISTED' and not result['fully_observed']
    assert result['items'][0]['reason_codes'] == ['PERSISTED_RESULT_NOT_FOUND']


@pytest.mark.parametrize('size', [1, 100, 1000])
def test_complete_rows_use_bounded_set_reads_not_per_row_sql(service, size):
    rows = prepare(service.mapper, [row(n, reference=f'fictional-{n}') for n in range(1, size + 1)])
    accept(service.mapper, rows)
    body = SourceReconcileInput(**payload(service, rows))
    service.db.rollback()
    queries = []
    def sql(_connection, _cursor, statement, *_):
        if statement.lstrip().upper().startswith('SELECT'):
            queries.append(statement)
    engine = service.db.get_bind()
    event.listen(engine, 'before_cursor_execute', sql)
    try:
        result = ImportSourceService(service.db).reconcile(body)
    finally:
        event.remove(engine, 'before_cursor_execute', sql)
    assert result['fully_observed'] and len(result['items']) == size
    assert len(result['facts']) == len(result['outputs']) == size
    assert len(queries) < 70
    source_queries = [value for value in queries if 'source_row_number IN' in value or
        '(transaction_import_row.transaction_import_file_id, transaction_import_row.source_row_number) IN' in value]
    assert len(source_queries) <= (size + 399) // 400
    assert all('raw_payload' not in value for value in source_queries)


def test_read_endpoint_is_typed_strict_and_preserves_all_twenty_tables(client):
    response = client.post(BASE + '/import_file/reconcile', json=dict(rows=[dict(file_id=1, source_row_number=1)], files=[]))
    assert response.status_code == 200, response.text
    assert not response.json()['body']['fully_observed']
    assert response.json()['body']['items'][0]['reason_codes'] == ['SOURCE_FILE_IDENTITY_REQUIRED']
    response = client.post(BASE + '/import_file/reconcile?page_size=100', json=dict(rows=[dict(file_id=1, source_row_number=1)], files=[]))
    assert response.status_code == 422 and response.json()['body']['code'] == 'LIST_PARAMETER_NOT_SUPPORTED'
    schema = client.get('/openapi.json').json()
    operation = schema['paths'][BASE + '/import_file/reconcile']['post']
    assert operation['requestBody']['content']['application/json']['schema']['$ref'].endswith('/SourceReconcileInput')
    assert operation['responses']['200']['content']['application/json']['schema']['$ref'].endswith('/SourceReconcileResponse')


@pytest.mark.parametrize('change', ['bool', 'duplicate', '1001', 'bad_hash', 'unknown_field', 'auto_target'])
def test_dedicated_batch_contract_rejects_invalid_or_expanded_input(change):
    body = dict(rows=[dict(file_id=1, source_row_number=1)], files=[dict(file_id=1, sha256='a' * 64)])
    if change == 'bool': body['rows'][0]['file_id'] = True
    elif change == 'duplicate': body['rows'] *= 2
    elif change == '1001': body['rows'] = [dict(file_id=1, source_row_number=n) for n in range(1,1002)]
    elif change == 'bad_hash': body['files'][0]['sha256'] = 'A' * 64
    elif change == 'unknown_field': body['receipt'] = 'not-a-receipt'
    else: body['rows'][0]['target'] = dict(kind='FACT', transaction_id=1)
    with pytest.raises(ValidationError):
        SourceReconcileInput(**body)
