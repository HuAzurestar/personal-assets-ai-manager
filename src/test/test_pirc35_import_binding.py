"""Complete readonly binding checks; fictional source identities only."""
from copy import deepcopy
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import event, update

from backend.core.import_preview_store import import_preview_store
from backend.entity import LedgerAccountRef, LedgerAccount, LedgerAccountParty
from backend.error import TargetIntakeError
from test_pirc35_import_service import service
from test_pirc35_import_api import client, BASE, preview, page
from test_pirc35_import_batch import prepare, row, accept
from test_pirc35_import_confirm_preview import install
from test_pirc35_import_duplicate import manifest


def refs(service):
    values = [LedgerAccountRef(name='Mock reliable A', source_namespace='ccb:statement-v1',
        source_identity='0000000000123456', identity_strength=1),
        LedgerAccountRef(name='Mock reliable B same tail', source_namespace='ccb:statement-v1',
        source_identity='9990000000123456', identity_strength=1),
        LedgerAccountRef(name='Mock weak same tail', reference='0000000000123456')]
    service.db.add_all(values)
    service.db.commit()
    return [item.id for item in values]


def payload(current, choices, ref):
    return dict(expected_updated_time=current['updated_time'],preview_digest=current['preview_digest'],
        account_ref_id=ref,choices=[dict(file_id=key[0],source_row_number=key[1],**choice) for key,choice in choices.items()])


def binding(service, current, choices, ref):
    from backend.schema.import_command import ImportBindingPreviewInput
    return service.binding_preview(current['token'],ImportBindingPreviewInput(**payload(current,choices,ref)))


def test_http_binding_readonly_and_concrete_schema(client):
    current = preview(client)
    rows = page(client,current)['items'][:2]
    response = client.post(BASE+f"/preview/{current['token']}/binding-preview",json=dict(
        expected_updated_time=current['updated_time'],preview_digest=current['preview_digest'],account_ref_id=0,
        choices=[{key:row[key] for key in ('file_id','source_row_number')} | dict(decision='ACCEPT') for row in rows]))
    assert response.status_code == 200,response.text
    result = response.json()['body']
    assert result['selected_count'] == 2 and all(item['applicable'] for item in result['items'])
    assert result['source_preview_digest'] == current['preview_digest'] and result['account_ref_id'] == 0
    latest = client.get(BASE+f"/preview/{current['token']}").json()['body']
    assert latest == current
    schema = client.get('/openapi.json').json()
    route = schema['paths'][BASE+'/preview/{token}/binding-preview']['post']
    assert route['requestBody']['content']['application/json']['schema']['$ref'].endswith('ImportBindingPreviewInput')
    assert 'ImportBindingPreviewResponse' in str(route['responses']['200'])
    assert schema['components']['schemas']['ImportBindingPreviewInput']['properties']['choices']['maxItems'] == 20000
    assert schema['components']['schemas']['ImportReviseInput']['properties']['choices']['maxItems'] == 1000


def test_full_identity_not_masked_tail_and_nothing_published(service):
    a,b,weak = refs(service)
    rows = prepare(service.mapper,[row(1,reference='A'),row(2,reference='B',
        account=dict(number='9990000000123456'), source_account=dict(source_namespace='ccb:statement-v1',
            source_identity='9990000000123456',identity_strength='RELIABLE'))])
    choices = {key:dict(decision='ACCEPT') for key in rows}
    current = install(service,rows,{})
    retained,before = service.store.get(current['token']),manifest(service)
    result = binding(service,current,choices,a)
    assert [item['applicable'] for item in result['items']] == [True,False]
    assert result['items'][1]['reason_codes'] == ['ACCOUNT_BINDING_CONFLICT']
    assert all(item['source_state'] == 'RELIABLE' for item in result['items'])
    for value in ('0000000000123456','9990000000123456','raw_payload','auto_identity','chain'):
        assert value not in str(result)
    assert service.store.get(current['token']) == retained and manifest(service) == before
    assert all(not item['applicable'] for item in binding(service,current,choices,weak)['items'])
    assert all(item['reason_codes'] == ['REFERENCE_NOT_FOUND'] for item in binding(service,current,choices,9999)['items'])
    assert all(item['applicable'] for item in binding(service,current,choices,None)['items'])
    assert all(item['applicable'] for item in binding(service,current,choices,0)['items'])
    service.db.execute(update(LedgerAccountRef).where(LedgerAccountRef.id==a).values(status='CLOSED'))
    service.db.commit()
    assert binding(service,current,choices,a)['items'][0]['reason_codes'] == ['ACCOUNT_NOT_ACTIVE']


def test_unknown_source_manual_assignment_does_not_fabricate_identity(service):
    a,_,_ = refs(service)
    rows = prepare(service.mapper,[row(1,reference='unknown',source_account=dict(identity_strength='UNKNOWN'))])
    current = install(service,rows,{})
    retained = service.store.get(current['token'])
    result = binding(service,current,{key:dict(decision='ACCEPT') for key in rows},a)
    assert result['items'][0]['applicable'] and result['items'][0]['source_state'] == 'UNKNOWN'
    assert service.store.get(current['token']) == retained


def test_existing_processed_link_and_old_unrechecked_are_explicit_exceptions(service):
    a,_,_ = refs(service)
    original = prepare(service.mapper,[row(1,reference='accepted'),row(2,reference='oldskip')])
    accept(service.mapper,original,{key:dict(decision='ACCEPT' if key[1]==1 else 'SKIP') for key in original})
    repeated = prepare(service.mapper,[row(1,reference='accepted')],sha='b'*64)
    other = prepare(service.mapper,[row(1,reference='link'),row(2,reference='new')],sha='c'*64)
    rows = original | repeated | other
    current = install(service,rows,{})
    choices = {key:dict(decision='ACCEPT') for key in rows}
    choices[next(iter(other))] |= dict(resolution='LINK_EXISTING',target=dict(kind='FACT',transaction_id=1))
    result = binding(service,current,choices,a)
    found = {(item['row']['file_id'],item['row']['source_row_number']):item for item in result['items']}
    assert found[next(iter(original))]['reason_codes'] == ['ROWS_ALREADY_PROCESSED']
    old = list(original)[1]
    assert found[old]['reason_codes'] == ['ROW_RECHECK_REQUIRED']
    assert found[next(iter(repeated))]['reason_codes'] == ['EXISTING_ACCOUNT_READ_ONLY']
    assert found[next(iter(other))]['reason_codes'] == ['EXISTING_ACCOUNT_READ_ONLY']
    assert found[list(other)[1]]['applicable']
    choices[old]['recheck'] = True
    assert next(item for item in binding(service,current,choices,a)['items'] if item['row']['source_row_number']==2 and item['row']['file_id']==old[0])['applicable']


def test_binding_projects_complete_cached_source_group_not_only_selected_prefix(service):
    a,_,_ = refs(service)
    rows = prepare(service.mapper,[row(1),row(2)]) # One reliable Fact identity, two evidence rows.
    choices = {key:dict(decision='ACCEPT') for key in rows}
    current = install(service,rows,choices)
    first = {next(iter(rows)):dict(decision='ACCEPT')}
    assert binding(service,current,first,a)['items'][0]['reason_codes'] == ['ACCOUNT_BINDING_CONFLICT']
    assert all(item['applicable'] for item in binding(service,current,choices,a)['items'])


def test_account_chain_and_preview_change_are_rechecked_without_mutating_cache(service,monkeypatch):
    a,_,_ = refs(service)
    party = LedgerAccountParty(name='Mock owner',status='CLOSED')
    service.db.add(party);service.db.flush()
    account = LedgerAccount(name='Mock set',party_id=party.id)
    service.db.add(account);service.db.flush()
    service.db.execute(update(LedgerAccountRef).where(LedgerAccountRef.id==a).values(account_id=account.id))
    service.db.commit()
    rows = prepare(service.mapper,[row()])
    current = install(service,rows,{})
    choices = {key:dict(decision='ACCEPT') for key in rows}
    assert binding(service,current,choices,a)['items'][0]['reason_codes'] == ['ACCOUNT_NOT_ACTIVE']
    original = service.mapper.account_premises
    def changed(input_rows,input_choices):
        result = original(input_rows,input_choices)
        state = service.store.get(current['token'])
        service.store.replace(state,state.updated_time)
        return result
    monkeypatch.setattr(service.mapper,'account_premises',changed)
    with pytest.raises(TargetIntakeError,match='PREVIEW_CHANGED'):
        binding(service,current,choices,a)


def test_binding_select_count_is_bounded_and_no_writes(service):
    a,_,_ = refs(service)
    rows = prepare(service.mapper,[row(n,reference=f'bind-{n}') for n in range(1,101)])
    current = install(service,rows,{})
    counts = []
    for size in (1,100):
        statements = []
        def capture(_connection,_cursor,statement,_parameters,_context,_many):
            statements.append(statement)
        event.listen(service.db.bind,'before_cursor_execute',capture)
        try:
            result = binding(service,current,{key:dict(decision='ACCEPT') for key in list(rows)[:size]},a)
        finally: event.remove(service.db.bind,'before_cursor_execute',capture)
        assert len(result['items']) == size and all(item['applicable'] for item in result['items'])
        assert not any(statement.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for statement in statements)
        assert sum(statement.lstrip().upper().startswith('BEGIN') for statement in statements) <= 1
        counts.append(sum(statement.lstrip().upper().startswith('SELECT') for statement in statements))
    assert counts[0] == counts[1]


def test_binding_wal_account_chain_uses_one_snapshot_and_next_read_detects_change(service,monkeypatch):
    a,_,_ = refs(service)
    service.db.rollback()
    service.db.connection().exec_driver_sql('PRAGMA journal_mode=WAL')
    service.db.rollback()
    rows = prepare(service.mapper,[row()])
    current = install(service,rows,{})
    original = service.mapper.account_premises
    changed = False
    def interleave(input_rows,input_choices):
        nonlocal changed
        result = original(input_rows,input_choices)
        if not changed:
            changed = True
            with service.db.bind.begin() as writer:
                writer.execute(update(LedgerAccountRef).where(LedgerAccountRef.id==a).values(status='CLOSED'))
        return result
    monkeypatch.setattr(service.mapper,'account_premises',interleave)
    choices = {key:dict(decision='ACCEPT') for key in rows}
    assert binding(service,current,choices,a)['items'][0]['applicable']
    assert binding(service,current,choices,a)['items'][0]['reason_codes'] == ['ACCOUNT_NOT_ACTIVE']


def test_binding_stale_busy_missing_and_oversized_scope_do_not_publish(service):
    from backend.schema.import_command import ImportBindingPreviewInput
    rows = prepare(service.mapper,[row()])
    current = install(service,rows,{})
    choices = {key:dict(decision='ACCEPT') for key in rows}
    retained,before = service.store.get(current['token']),manifest(service)
    with pytest.raises(TargetIntakeError,match='PREVIEW_CHANGED'):
        binding(service,current | dict(updated_time=retained.updated_time+timedelta(microseconds=1)),choices,0)
    with service.store.claim(current['token'],retained.updated_time):
        with pytest.raises(TargetIntakeError,match='PREVIEW_BUSY'): binding(service,current,choices,0)
    with pytest.raises(TargetIntakeError,match='PREVIEW_ROW_NOT_FOUND'):
        binding(service,current,{(999,1):dict(decision='ACCEPT')},0)
    values = payload(current,choices,0)
    for invalid in ([],values['choices']*2,[dict(file_id=1,source_row_number=i+1,decision='ACCEPT') for i in range(20001)]):
        with pytest.raises(ValidationError): ImportBindingPreviewInput(**(values | dict(choices=invalid)))
    assert service.store.get(current['token']) == retained and manifest(service) == before


@pytest.mark.parametrize('value',[True,'1',1.5,-1])
def test_binding_target_and_rows_are_strict(value):
    from backend.schema.import_command import ImportBindingPreviewInput
    with pytest.raises(ValidationError):
        ImportBindingPreviewInput(expected_updated_time='2026-10-03T00:00:00Z',preview_digest='a'*64,
            account_ref_id=value,choices=[dict(file_id=1,source_row_number=1,decision='ACCEPT')])
