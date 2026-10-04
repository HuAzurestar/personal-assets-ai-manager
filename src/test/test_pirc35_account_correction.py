"""Account-only intent must retain complete immutable output graphs."""
import pytest
from sqlalchemy import select

from backend.entity import LedgerAccountRef, LedgerEntry, PositionLeg, ReviewCase, TransactionFact
from backend.error import TargetEconomicError
from backend.schema.review_command import ReviewChangeInput, ReviewCommandInput
from backend.service.review_command_service import ReviewCommandService
from test_pirc35_review_command import db, borrowed, counts, execute
from test_pirc35_tag_semantics import tagged


def refs(db):
    db.add_all([LedgerAccountRef(id=i, name=f'Mock source {i}', account_id=0, status='ACTIVE') for i in (1, 2)])
    db.commit()


def correction(ledger_id, ref=1):
    return dict(account_corrections=[dict(ledger_id=ledger_id, account_ref_id=ref)])


def signature(detail):
    ledger_fact={row['ledger_id']:row['transaction_id'] for row in detail['allocations']}
    ledgers=[(ledger_fact[row['id']],row['economic_type'],row['cash_direction'],row['cash_amount'],
        row['cash_currency_code'],row['account_ref_id'],row['occurred_time']) for row in detail['ledger_entries']]
    legs=[(row['position_id'],row['type'],row['leg_amount'],row['leg_direction'],row['occurred_time'],row['basis'])
        for row in detail['position_legs']]
    ledger_index={row['id']:index for index,row in enumerate(detail['ledger_entries'])}
    leg_index={row['id']:index for index,row in enumerate(detail['position_legs'])}
    links=[(ledger_index[row['ledger_id']],leg_index[row['position_leg_id']],row['cash_amount'],row['cash_currency_code'])
        for row in detail['position_allocations']]
    return ledgers,legs,links


def test_correction_copies_whole_split_group_changes_only_named_ledger(db):
    refs(db)
    draft=borrowed()
    parameters=draft['parameters']
    parameters['allocations'][0]['cash_amount']=30000
    parameters['allocations'][0]['account_ref_id']=2
    parameters['allocations'] += [dict(transaction_id=1,economic_type='TRANSACTION',cash_amount=10000,account_ref_id=2),
        dict(transaction_id=3,economic_type='TRANSACTION',cash_amount=10000,account_ref_id=2)]
    parameters['legs'][0]['leg_amount']=30000
    parameters['position_allocations'][0]['cash_amount']=30000
    service=ReviewCommandService(db)
    rid=execute(db,new_reviews=[draft])['created_reviews'][0]['id']
    old=service.detail(rid)
    change=correction(old['ledger_entries'][0]['id'])
    before=counts(db)
    preview=service.preview(ReviewChangeInput(**change))
    assert not preview['blocking_issues'] and counts(db)==before
    assert len(preview['new_reviews'])==1 and len(preview['new_reviews'][0]['allocations'])==3
    result=service.command(ReviewCommandInput(**change,expected_reviews=preview['expected_reviews'],preview_digest=preview['preview_digest']))
    new=service.detail(result['created_reviews'][0]['id'])
    expected=signature(old)
    expected[0][0]=expected[0][0][:5]+(1,)+expected[0][0][6:]
    assert signature(new)==expected
    assert new['type']==old['type'] and new['title']==old['title']
    preserved=service.detail(rid)
    assert preserved['status']=='REVOKED'
    for key in ('ledger_entries','allocations','position_legs','position_allocations','positions'):
        assert preserved[key]==old[key]
    assert counts(db)[-1]==0


def test_correction_remaps_active_reduction_group_without_changing_quantity(db):
    refs(db)
    service=ReviewCommandService(db)
    opening=execute(db,new_reviews=[borrowed()])
    rid=opening['created_reviews'][0]['id'];pid=opening['created_positions'][0]['id']
    original=service.detail(rid);source=original['position_legs'][0]['id']
    repayment=execute(db,new_reviews=[borrowed(source,pid)])['created_reviews'][0]['id']
    old_repayment=service.detail(repayment)
    result=execute(db,**correction(original['ledger_entries'][0]['id']))
    assert len(result['created_reviews'])==2
    assert result['consumer_state']['position_states']==[dict(position_id=pid,quantity_state='KNOWN',quantity=10000)]
    copied=[service.detail(row['id']) for row in result['created_reviews']]
    new_source=next(leg['id'] for detail in copied for leg in detail['position_legs'] if leg['leg_direction']=='IN')
    new_out=next(leg for detail in copied for leg in detail['position_legs'] if leg['leg_direction']=='OUT')
    assert new_out['source_position_leg_id']==new_source and new_source!=source
    assert signature(next(detail for detail in copied if detail['id']==new_out['review_id']))==signature(old_repayment)
    assert db.get(ReviewCase,rid).status==db.get(ReviewCase,repayment).status==1
    assert service.detail(repayment)['position_legs']==old_repayment['position_legs']
    assert len(db.scalars(select(PositionLeg).where(PositionLeg.position_id==pid)).all())==4


@pytest.mark.parametrize('stage',['identities','outputs','relations','tags'])
def test_correction_fault_rolls_back_entire_clone_and_source_mapping(db,stage):
    refs(db)
    service=ReviewCommandService(db)
    opening=execute(db,new_reviews=[borrowed()])
    rid=opening['created_reviews'][0]['id'];pid=opening['created_positions'][0]['id']
    original=service.detail(rid)
    repayment=execute(db,new_reviews=[borrowed(original['position_legs'][0]['id'],pid)])['created_reviews'][0]['id']
    change=correction(original['ledger_entries'][0]['id'])
    preview=service.preview(ReviewChangeInput(**change));before=counts(db)
    def fault(actual):
        if actual==stage: raise RuntimeError('Mock correction rollback')
    with pytest.raises(RuntimeError,match='Mock correction rollback'):
        service.command(ReviewCommandInput(**change,expected_reviews=preview['expected_reviews'],preview_digest=preview['preview_digest']),fault=fault)
    assert counts(db)==before
    assert db.get(ReviewCase,rid).status==db.get(ReviewCase,repayment).status==0
    assert service.detail(rid)['position_legs']==original['position_legs']


def test_default_correction_does_not_create_a_second_system_default(db):
    refs(db)
    result=execute(db,**correction(1))
    assert result['created_reviews'][0]['type']=='OTHER_MANUAL'
    assert db.get(ReviewCase,1).behavior_type==0 and db.get(ReviewCase,1).status==1
    assert len(db.scalars(select(ReviewCase).where(ReviewCase.behavior_type==0)).all())==4
    execute(db,deactivate_review_ids=[result['created_reviews'][0]['id']])
    assert db.get(ReviewCase,1).status==0 and db.get(LedgerEntry,1).account_ref_id==0


@pytest.mark.parametrize('target,code',[(0,'ACCOUNT_CORRECTION_NO_CHANGE'),(999,'ACCOUNT_RELATION_BROKEN')])
def test_invalid_account_correction_has_no_partial_effect(db,target,code):
    refs(db);service=ReviewCommandService(db);before=counts(db)
    preview=service.preview(ReviewChangeInput(**correction(1,target)))
    assert preview['blocking_issues'][0]['code']==code and counts(db)==before


def test_stale_correction_and_post_commit_unknown_cannot_replay(db):
    refs(db);service=ReviewCommandService(db);change=correction(1)
    preview=service.preview(ReviewChangeInput(**change))
    command=ReviewCommandInput(**change,expected_reviews=preview['expected_reviews'],preview_digest=preview['preview_digest'])
    def lost(stage):
        if stage=='response': raise RuntimeError('Mock lost response')
    with pytest.raises(TargetEconomicError) as error: service.command(command,fault=lost)
    assert error.value.code=='RESULT_UNKNOWN'
    before=counts(db)
    with pytest.raises(TargetEconomicError) as stale: service.command(command)
    assert stale.value.code=='ACCOUNT_CORRECTION_NOT_ACTIVE' and counts(db)==before


def test_quantity_only_dependent_review_is_copied_as_a_whole(db):
    refs(db);service=ReviewCommandService(db)
    opening=execute(db,new_reviews=[borrowed()]);rid=opening['created_reviews'][0]['id'];pid=opening['created_positions'][0]['id']
    original=service.detail(rid);source=original['position_legs'][0]['id']
    reduction=dict(case_code='POS_POSITION_SETTLE',title='Mock evidenced remission',new_positions=[],allocations=[],
        legs=[dict(existing_position_id=pid,type='MOVEMENT',leg_amount=10000,leg_direction='OUT',source=source,
            occurred_time='2024-01-02T00:00:00Z',basis='Mock remission')],position_allocations=[])
    dependent=execute(db,new_reviews=[reduction])['created_reviews'][0]['id']
    preview=service.preview(ReviewChangeInput(**correction(original['ledger_entries'][0]['id'])))
    assert dependent in preview['impact']['conflicting_review_ids']
    result=execute(db,**correction(original['ledger_entries'][0]['id']))
    assert len(result['created_reviews'])==2 and db.get(ReviewCase,dependent).status==1
    assert result['consumer_state']['position_states']==[dict(position_id=pid,quantity_state='KNOWN',quantity=30000)]


def test_same_group_multi_ledger_correction_does_not_copy_it_twice(db):
    refs(db)
    from test_pirc35_review_command import normal
    service=ReviewCommandService(db);rid=execute(db,new_reviews=[normal(1,3)])['created_reviews'][0]['id']
    original=service.detail(rid)
    change=dict(account_corrections=[dict(ledger_id=row['id'],account_ref_id=1) for row in original['ledger_entries']])
    preview=service.preview(ReviewChangeInput(**change))
    assert len(preview['new_reviews'])==1 and len(preview['new_reviews'][0]['account_changes'])==2
    result=execute(db,**change)
    assert len(result['created_reviews'])==1


def test_correction_requires_exact_fresh_duplicate_keeper_and_revalidates_sources(db):
    refs(db)
    from test_pirc35_review_command import normal
    from backend.mapper.review_command_mapper import ReviewCommandMapper
    # Create a new fictional Fact; do not alter any accepted original output.
    original=db.get(TransactionFact,1)
    db.add(TransactionFact(id=5,fact_key='Mock correction duplicate',amount=40000,currency_code='CNY',
        cash_direction=2,occurred_time=original.occurred_time,account_code=''))
    db.flush();ReviewCommandMapper(db).create_initial_defaults([5])
    db.commit()
    kept=execute(db,new_reviews=[normal(1)|dict(account_bindings=[dict(transaction_id=1,account_ref_id=1)])])['created_reviews'][0]['id']
    duplicate=dict(case_code='DUPLICATE',title='Mock duplicate',parameters=dict(transaction_ids=[5]),
        account_bindings=[dict(transaction_id=5,account_ref_id=2)],duplicate_transactions=[dict(transaction_id=5,kept_transaction_id=1)])
    service=ReviewCommandService(db);rid=execute(db,new_reviews=[duplicate])['created_reviews'][0]['id']
    old=service.detail(rid);lid=old['ledger_entries'][0]['id'];before=counts(db)
    change=correction(lid,1)
    required=service.preview(ReviewChangeInput(**change))['blocking_issues'][0]
    assert required['code']=='ACCOUNT_CORRECTION_KEEPER_REQUIRED'
    assert required['details']=={'duplicate_transaction_ids':[5]}
    change['correction_duplicates']=[dict(transaction_id=5,kept_transaction_id=1)]
    assert service.preview(ReviewChangeInput(**change))['blocking_issues'][0]['code']=='INVALID_DUPLICATE'
    assert counts(db)==before and db.get(ReviewCase,rid).status==db.get(ReviewCase,kept).status==0
    db.add(LedgerAccountRef(id=3,name='Mock third source',account_id=0,status='ACTIVE'));db.commit()
    change['account_corrections'][0]['account_ref_id']=3
    result=execute(db,**change)
    new=service.detail(result['created_reviews'][0]['id'])
    assert new['ledger_entries'][0]['economic_type']=='DUPLICATE' and new['ledger_entries'][0]['account_ref_id']==3
    assert not new['position_legs'] and db.get(ReviewCase,kept).status==0


@pytest.mark.parametrize('payload',[
    dict(account_corrections=[dict(ledger_id=True,account_ref_id=1)]),
    dict(account_corrections=[dict(ledger_id=1,account_ref_id=1,cash_amount=1)]),
    dict(account_corrections=[dict(ledger_id=1,account_ref_id=1)]*2),
    dict(account_corrections=[dict(ledger_id=1,account_ref_id=1)],deactivate_review_ids=[1]),
    dict(correction_duplicates=[dict(transaction_id=1,kept_transaction_id=2)],activate_review_ids=[1]),
])
def test_correction_accepts_no_client_accounting_or_mixed_decisions(payload):
    from pydantic import ValidationError
    with pytest.raises(ValidationError): ReviewChangeInput(**payload)


def test_account_only_http_intent_reuses_atomic_publication_without_output_write_api(db):
    from fastapi.testclient import TestClient
    from backend.target_main import app
    refs(db)
    with TestClient(app) as client:
        change=correction(1)
        response=client.post('/paam/ledger/v1/review/preview',json=change)
        assert response.status_code==200,response.text
        preview=response.json()['body']
        assert not preview['blocking_issues']
        assert preview['new_reviews'][0]['source_review_id']==1
        assert preview['new_reviews'][0]['account_changes']==[dict(ledger_id=1,allocation_index=0,before_account_ref_id=0,after_account_ref_id=1)]
        command=change|dict(expected_reviews=preview['expected_reviews'],preview_digest=preview['preview_digest'])
        result=client.post('/paam/ledger/v1/review/command',json=command)
        assert result.status_code==200,result.text
        assert client.get('/paam/ledger/v1/flow/1').json()['body']['active'] is False
        assert set(app.openapi()['components']['schemas']['AccountCorrection']['properties'])=={'ledger_id','account_ref_id'}


def test_correction_preserves_only_unique_unchanged_tag_meaning(tagged):
    from test_pirc35_tag_semantics import assign,tags
    from test_pirc35_review_command import normal
    db,view_id,default,food=tagged
    refs(db);service=ReviewCommandService(db)
    old=service.detail(execute(db,new_reviews=[normal(1,3)])['created_reviews'][0]['id'])
    for flow in old['ledger_entries']: assign(db,flow['id'],food)
    result=execute(db,**correction(old['ledger_entries'][0]['id']))
    new=service.detail(result['created_reviews'][0]['id'])
    assert tags(db,new['ledger_entries'][0]['id'])==[default]
    assert tags(db,new['ledger_entries'][1]['id'])==[food]
    assert all(tags(db,flow['id'])==[food] for flow in old['ledger_entries'])


def test_remapped_quantity_source_is_not_claimed_identical_for_tags(tagged):
    from test_pirc35_tag_semantics import assign,tags
    db,view_id,default,food=tagged;refs(db);service=ReviewCommandService(db)
    opening=execute(db,new_reviews=[borrowed()]);old=service.detail(opening['created_reviews'][0]['id'])
    repayment=execute(db,new_reviews=[borrowed(old['position_legs'][0]['id'],opening['created_positions'][0]['id'])])
    old_out=service.detail(repayment['created_reviews'][0]['id'])
    assign(db,old_out['ledger_entries'][0]['id'],food)
    change=correction(old['ledger_entries'][0]['id'])
    preview=service.preview(ReviewChangeInput(**change))
    assert not any(row['disposition']=='KEEP' and row['old_ledger_id']==old_out['ledger_entries'][0]['id']
        for row in preview['tag_effect']['mappings'])
    result=execute(db,**change)
    new_out=next(service.detail(row['id']) for row in result['created_reviews'] if row['title']==old_out['title']
        and service.detail(row['id'])['position_legs'][0]['leg_direction']=='OUT')
    assert tags(db,new_out['ledger_entries'][0]['id'])==[default]
    assert tags(db,old_out['ledger_entries'][0]['id'])==[food]


def test_revoked_consumer_is_not_activated_or_rewritten_by_correction(db):
    refs(db);service=ReviewCommandService(db)
    opening=execute(db,new_reviews=[borrowed()]);original=service.detail(opening['created_reviews'][0]['id'])
    pid=opening['created_positions'][0]['id']
    repayment=execute(db,new_reviews=[borrowed(original['position_legs'][0]['id'],pid)])['created_reviews'][0]['id']
    execute(db,deactivate_review_ids=[repayment]);old_out=service.detail(repayment)
    result=execute(db,**correction(original['ledger_entries'][0]['id']))
    assert len(result['created_reviews'])==1
    assert result['consumer_state']['position_states']==[dict(position_id=pid,quantity_state='KNOWN',quantity=40000)]
    assert service.detail(repayment)==old_out


def test_correction_does_not_refresh_legacy_ledger_source_text_from_fact(db):
    refs(db)
    # Fictional legacy metadata differs from immutable Fact text. The account
    # command must not "repair" either as a side effect of changing ref.
    db.get(LedgerEntry,1).account_code='Mock legacy own text'
    db.get(LedgerEntry,1).counterparty_account_ref='Mock legacy counterpart'
    db.commit()
    result=execute(db,**correction(1))
    outputs=ReviewCommandService(db).detail(result['created_reviews'][0]['id'])['ledger_entries']
    new=db.get(LedgerEntry,outputs[0]['id'])
    assert new.account_code==db.get(LedgerEntry,1).account_code=='Mock legacy own text'
    assert new.counterparty_account_ref==db.get(LedgerEntry,1).counterparty_account_ref=='Mock legacy counterpart'
    assert db.get(TransactionFact,1).account_code==''
