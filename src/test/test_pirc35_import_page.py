"""Page guard avoids a second full source copy without weakening isolation."""
from copy import deepcopy
from datetime import timedelta

import pytest

from backend.core import import_preview_store as store_module
from backend.error import TargetIntakeError
from backend.mapper.import_batch_mapper import fingerprint
from backend.schema.import_command import PreviewRowListRequest
from backend.service.import_risk_service import ImportRiskService
from test_pirc35_import_service import service
from test_pirc35_import_batch import prepare,row
from test_pirc35_import_confirm_preview import install
from test_pirc35_import_duplicate import manifest


def test_row_page_has_one_detached_copy_and_full_guard(service,monkeypatch):
    rows=prepare(service.mapper,[row(n,reference=f'Mock-{n}') for n in range(1,41)])
    current=install(service,rows,{key:dict(decision='ACCEPT',resolution='NEW',acknowledge_new_risk=True) for key in rows})
    original=service.store.get
    before=original(current['token'])
    financial=manifest(service)
    calls=[]
    def get(token):
        calls.append(token)
        return original(token)
    monkeypatch.setattr(service.store,'get',get)
    page=service.row_page(current['token'],current['preview_digest'],PreviewRowListRequest(page_size=20,page_index=2))
    assert calls == [current['token']] # A second full clone is the measured hot path.
    assert page['total'] == 40 and len(page['items']) == 20
    assert [item['source_row_number'] for item in page['items']] == list(range(21,41))
    assert before.digest() == fingerprint(dict(files=before.files,candidates=[dict(identity=list(key),
        premise=candidate['premise_hash'],choice=before.choices.get(key)) for key,candidate in sorted(before.candidates.items())]))
    page['items'][0]['choice']['decision']='SKIP'
    page['items'][0]['parsed']['summary']='changed client projection'
    assert original(current['token']) == before and manifest(service) == financial


@pytest.mark.parametrize('mode',['replace','evict'])
def test_late_row_page_changes_still_reject_the_whole_result(service,monkeypatch,mode):
    rows=prepare(service.mapper,[row()])
    current=install(service,rows,{})
    original=ImportRiskService.plan
    def late(risk,*args,**kwargs):
        result=original(risk,*args,**kwargs)
        state=service.store.get(current['token'])
        if mode=='replace':
            state.choices[next(iter(rows))]=dict(decision='SKIP')
            service.store.replace(state,state.updated_time)
        else:service.store.remove(current['token'])
        return result
    monkeypatch.setattr(ImportRiskService,'plan',late)
    with pytest.raises(TargetIntakeError) as error:
        service.row_page(current['token'],current['preview_digest'],PreviewRowListRequest())
    assert error.value.code == ('PREVIEW_CHANGED' if mode=='replace' else 'PREVIEW_UNAVAILABLE')


def test_cache_local_guard_checks_both_fields_without_copy_or_mutable_return(service,monkeypatch):
    rows=prepare(service.mapper,[row()])
    current=install(service,rows,{})
    state=service.store.get(current['token'])
    digest=state.digest()
    def no_copy(*_args,**_kwargs):raise AssertionError('guard must not clone source envelopes')
    with monkeypatch.context() as patch:
        patch.setattr(store_module.copy,'deepcopy',no_copy)
        assert service.store.ensure_current(state.token,state.updated_time,digest) is None
        for time,value in [(state.updated_time+timedelta(microseconds=1),digest),(state.updated_time,'0'*64)]:
            with pytest.raises(TargetIntakeError) as error:
                service.store.ensure_current(state.token,time,value)
            assert error.value.code == 'PREVIEW_CHANGED'
    # A replacement advances time; both the old input and a mismatched digest
    # remain invalid. The caller never receives the mutable cached object.
    revised=deepcopy(state)
    revised.choices[next(iter(rows))]=dict(decision='SKIP')
    service.store.replace(revised,state.updated_time)
    with pytest.raises(TargetIntakeError) as error:
        service.store.ensure_current(state.token,state.updated_time,digest)
    assert error.value.code == 'PREVIEW_CHANGED'
    service.store.ensure_current(revised.token,revised.updated_time,revised.digest())
    with service.store.claim(revised.token,revised.updated_time) as lease:
        # Reading a CONFIRMING preview remains permitted, as on the old route.
        service.store.ensure_current(revised.token,revised.updated_time,revised.digest())
        lease.publish()
    with pytest.raises(TargetIntakeError) as error:
        service.store.ensure_current(revised.token,revised.updated_time,revised.digest())
    assert error.value.code == 'PREVIEW_CHANGED'
    service.store.remove(revised.token)
    with pytest.raises(TargetIntakeError) as error:
        service.store.ensure_current(revised.token,revised.updated_time,revised.digest())
    assert error.value.status_code == 410
