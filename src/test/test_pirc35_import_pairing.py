"""Readonly named bulk pairing; complete scope and fictional identities only."""
from copy import deepcopy
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import event, update

from backend.core.import_pairing import ExactPairingIndex
from backend.entity import TransactionFact, ReviewCase, LedgerEntry
from backend.error import TargetIntakeError, TargetEconomicError
from backend.schema.import_command import ImportPairingPreviewInput
from backend.schema.import_batch_read import ImportPairingPreviewPO
from test_pirc35_import_service import service
from test_pirc35_import_api import client, BASE, preview, page
from test_pirc35_import_batch import prepare, row, accept
from test_pirc35_import_confirm_preview import install
from test_pirc35_import_duplicate import manifest


def choice(**changes):
    return dict(decision="ACCEPT",recheck=False,account_ref_id=None,resolution="AUTO",target=None,acknowledge_new_risk=False) | changes


def payload(current, choices, kind="SAME_SOURCE"):
    return dict(expected_updated_time=current["updated_time"],preview_digest=current["preview_digest"],kind=kind,
        choices=[dict(file_id=key[0],source_row_number=key[1],**value) for key,value in choices.items()])


def pairing(service, current, choices, kind="SAME_SOURCE"):
    result = service.pairing_preview(current["token"],ImportPairingPreviewInput(**payload(current,choices,kind)))
    ImportPairingPreviewPO(**result)
    return result


def paired(service, count=1, *, changes=None):
    originals = prepare(service.mapper,[row(n,reference="",amount_minor=-40000-n) for n in range(1,count+1)])
    accepted = accept(service.mapper,originals)
    incoming = prepare(service.mapper,[row(n,reference="",amount_minor=-40000-n,**(changes or {})) for n in range(1,count+1)],sha="b"*64)
    current = install(service,incoming,{})
    return current,incoming,accepted


def cross_identity():
    return dict(account=dict(number="9990000000123456"),source_account=dict(source_namespace="ccb:statement-v1",
        source_identity="9990000000123456",identity_strength="RELIABLE"))


def test_http_typed_complete_readonly_and_write_limit_unchanged(client):
    current = preview(client)
    selected = page(client,current)["items"][:3]
    choices = {(item["file_id"],item["source_row_number"]):choice() for item in selected}
    response = client.post(BASE+f"/preview/{current['token']}/pairing-preview",json=payload(current,choices))
    assert response.status_code == 200,response.text
    result = response.json()["body"]
    assert result["selected_count"] == len(result["items"]) == 3
    assert result["source_preview_digest"] == current["preview_digest"]
    assert client.get(BASE+f"/preview/{current['token']}").json()["body"] == current
    schema = client.get("/openapi.json").json()
    route = schema["paths"][BASE+"/preview/{token}/pairing-preview"]["post"]
    assert "ImportPairingPreviewResponse" in str(route["responses"]["200"])
    components = schema["components"]["schemas"]
    assert components["ImportPairingPreviewInput"]["properties"]["choices"]["maxItems"] == 20000
    assert components["ImportConfirmInput"]["properties"]["selected_rows"]["maxItems"] == 1000


def test_named_distinct_fact_pairs_complete_masked_and_readonly(service):
    current,rows,accepted = paired(service,30,changes=dict(note="Mock 1234567890123456 demo@example.org"))
    selected = {key:choice() for key in rows}
    before,retained = manifest(service),service.store.get(current["token"])
    result = pairing(service,current,selected)
    assert len(result["items"]) == 30
    assert all(item["state"] == "SUGGESTED" and item["candidate_count"] == 1 for item in result["items"])
    assert [item["suggestion"]["target"] for item in result["items"]] == [dict(kind="FACT",transaction_id=item["transaction_id"]) for item in accepted["processed_rows"]]
    assert all(item["suggestion"]["resolution"] == "LINK_EXISTING" for item in result["items"])
    assert all(item["suggestion"]["current_review_summaries"][0]["member_count"] == 1 for item in result["items"])
    assert "0000000000123456" not in str(result) and "raw_payload" not in str(result) and "chain" not in str(result)
    assert selected == {key:choice() for key in rows}
    assert service.store.get(current["token"]) == retained and manifest(service) == before


def test_cross_source_same_tail_different_complete_identity_and_final_keeper(service):
    current,rows,accepted = paired(service,changes=cross_identity())
    selected = {key:choice() for key in rows}
    assert pairing(service,current,selected)["items"][0]["state"] == "NO_MATCH"
    item = pairing(service,current,selected,"CROSS_SOURCE")["items"][0]
    assert item["state"] == "SUGGESTED" and item["suggestion"]["resolution"] == "DUPLICATE"
    assert item["suggestion"]["source_label_masked"] == "ccb ****3456"
    service.db.execute(update(ReviewCase).where(ReviewCase.id==accepted["processed_rows"][0]["created_review_id"]).values(status=1))
    service.db.commit()
    item = pairing(service,current,selected,"CROSS_SOURCE")["items"][0]
    assert item["state"] == "EXCEPTION" and item["candidate_count"] == 1
    assert item["suggestion"] is None and item["reason_codes"] == ["INVALID_DUPLICATE"]


def test_missing_origin_not_filtered_to_make_eligible_fact_unique(service):
    originals = prepare(service.mapper,[row(n,reference="") for n in (1,2)])
    accepted = accept(service.mapper,originals)
    id = accepted["processed_rows"][1]["transaction_id"]
    service.db.execute(update(TransactionFact).where(TransactionFact.id==id).values(fact_key="legacy-unknown"))
    service.db.commit()
    incoming = prepare(service.mapper,[row(reference="")],sha="b"*64)
    current = install(service,incoming,{})
    selected = {key:choice() for key in incoming}
    item = pairing(service,current,selected)["items"][0]
    assert item["state"] == "AMBIGUOUS" and item["candidate_count"] == 2 and item["suggestion"] is None
    item = pairing(service,current,selected,"CROSS_SOURCE")["items"][0]
    assert item["state"] == "EXCEPTION" and item["candidate_count"] == 1
    assert item["reason_codes"] == ["SOURCE_IDENTITY_REQUIRED"]


def test_same_file_two_sources_not_broadcast_to_one_fact_but_distinct_files_allowed(service):
    originals = prepare(service.mapper,[row(reference="")])
    accept(service.mapper,originals)
    incoming = prepare(service.mapper,[row(n,reference="") for n in (1,2)],sha="b"*64)
    current = install(service,incoming,{})
    items = pairing(service,current,{key:choice() for key in incoming})["items"]
    # Each also sees the other local row, so not a false unique Fact match.
    assert all(item["state"] == "AMBIGUOUS" and item["candidate_count"] == 2 for item in items)
    # Cross-source B rows are not one another's real keeper: both would point
    # to the one existing A. A same-file broadcast is explicitly blocked.
    incoming = prepare(service.mapper,[row(n,reference="",**cross_identity()) for n in (1,2)],sha="c"*64)
    service.store.remove(current["token"])
    current = install(service,incoming,{})
    items = pairing(service,current,{key:choice() for key in incoming},"CROSS_SOURCE")["items"]
    assert all(item["state"] == "EXCEPTION" and item["candidate_count"] == 1 and item["reason_codes"] == ["IDENTITY_AMBIGUOUS"] for item in items)
    # Two different files can each supply a separately confirmed B->A pair.
    second_file = prepare(service.mapper,[row(reference="",**cross_identity())],sha="d"*64)
    service.store.remove(current["token"])
    rows = {next(iter(incoming)):next(iter(incoming.values()))} | second_file
    current = install(service,rows,{})
    items = pairing(service,current,{key:choice() for key in rows},"CROSS_SOURCE")["items"]
    assert all(item["state"] == "SUGGESTED" for item in items)


@pytest.mark.parametrize("kind",["SAME_SOURCE","CROSS_SOURCE"])
def test_real_selected_row_anchor_named_no_fake_fact_id_and_no_cash_consent(service,kind):
    a = prepare(service.mapper,[row(reference="")])
    b = prepare(service.mapper,[row(reference="",**(cross_identity() if kind=="CROSS_SOURCE" else {}))],sha="b"*64)
    rows = a | b
    current = install(service,rows,{})
    selected = {key:choice() for key in rows}
    # Two AUTO rows cannot mutually become evidence/duplicate targets.
    first = pairing(service,current,selected,kind)["items"]
    assert all(item["suggestion"] is None and item["reason_codes"] == ["PAIR_TARGET_REQUIRES_REAL_ANCHOR"] for item in first)
    anchor = next(iter(a))
    selected[anchor] = choice(resolution="NEW",acknowledge_new_risk=True)
    before = deepcopy(selected)
    items = pairing(service,current,selected,kind)["items"]
    assert items[0]["state"] == "EXCEPTION" and items[0]["reason_codes"] == ["USER_INTENT_RETAINED"]
    assert items[1]["state"] == "SUGGESTED"
    suggestion = items[1]["suggestion"]
    assert suggestion["target"] == dict(kind="ROW",file_id=anchor[0],source_row_number=anchor[1])
    assert suggestion["current_review_summaries"] == []
    assert selected == before and not selected[next(iter(b))]["acknowledge_new_risk"]
    # Unselected ROW anchors are not inferred from another cached page.
    item = pairing(service,current,{next(iter(b)):choice()},kind)["items"][0]
    assert item["state"] == "NO_MATCH"


def test_processed_existing_skip_explicit_intents_old_states_are_retained(service):
    original = prepare(service.mapper,[row(1,reference="accepted"),row(2,reference="oldskip")])
    accept(service.mapper,original,{key:dict(decision="ACCEPT" if key[1]==1 else "SKIP") for key in original})
    incoming = prepare(service.mapper,[row(n,reference=ref) for n,ref in enumerate(("accepted","new","skip","manual"),1)],sha="b"*64)
    rows = original | incoming
    current = install(service,rows,{})
    selected = {key:choice() for key in rows}
    selected[list(incoming)[2]] = choice(decision="SKIP")
    selected[list(incoming)[3]] = choice(resolution="NEW",acknowledge_new_risk=True)
    found = {(item["row"]["file_id"],item["row"]["source_row_number"]):item for item in pairing(service,current,selected)["items"]}
    assert found[list(original)[0]]["reason_codes"] == ["ROWS_ALREADY_PROCESSED"]
    assert found[list(original)[1]]["reason_codes"] == ["ROW_RECHECK_REQUIRED"]
    assert found[list(incoming)[0]]["reason_codes"] == ["AUTO_IDENTITY_RETAINED"]
    assert found[list(incoming)[2]]["reason_codes"] == found[list(incoming)[3]]["reason_codes"] == ["USER_INTENT_RETAINED"]


def test_account_override_not_identity_and_link_does_not_need_new_binding(service):
    current,rows,_accepted = paired(service)
    selected = {key:choice(account_ref_id=9999) for key in rows}
    assert pairing(service,current,selected)["items"][0]["state"] == "SUGGESTED"
    rows_cross = prepare(service.mapper,[row(reference="",amount_minor=-40001,**cross_identity())],sha="c"*64)
    service.store.remove(current["token"])
    current_cross = install(service,rows_cross,{})
    item = pairing(service,current_cross,{key:choice(account_ref_id=9999) for key in rows_cross},"CROSS_SOURCE")["items"][0]
    assert item["state"] == "EXCEPTION" and item["reason_codes"] == ["REFERENCE_NOT_FOUND"]


@pytest.mark.parametrize("damage",["amount","time","currency","direction","unknown"])
def test_exact_core_or_unknown_source_not_manually_merged(service,damage):
    changes = {"amount":dict(amount_minor=-39999),"time":dict(occurred_at="2024-01-01T00:00:00.000001Z"),
        "currency":dict(currency="CNY_4"),"direction":dict(amount_minor=40001),"unknown":dict(account={},source_account={})}[damage]
    # Apply after preparing, so fixture helper cannot duplicate amount kwarg.
    originals = prepare(service.mapper,[row(reference="",amount_minor=-40001)])
    accept(service.mapper,originals)
    incoming = prepare(service.mapper,[row(reference="",amount_minor=-40001) | changes],sha="b"*64)
    current = install(service,incoming,{})
    item = pairing(service,current,{key:choice() for key in incoming})["items"][0]
    assert item["suggestion"] is None
    assert item["reason_codes"] == ["SOURCE_IDENTITY_REQUIRED"] if damage=="unknown" else item["state"] == "NO_MATCH"


def test_query_count_constant_one_or_hundred_and_no_mutation(service):
    current,rows,_accepted = paired(service,100)
    counts = []
    for size in (1,100):
        service.db.rollback()
        statements = []
        def observe(_connection,_cursor,statement,_parameters,_context,_many):
            statements.append(statement)
        event.listen(service.db.bind,"before_cursor_execute",observe)
        try:
            result = pairing(service,current,{key:choice() for key in list(rows)[:size]})
        finally:
            event.remove(service.db.bind,"before_cursor_execute",observe)
        assert all(item["state"] == "SUGGESTED" for item in result["items"])
        assert not any(sql.lstrip().upper().startswith(("INSERT","UPDATE","DELETE","BEGIN IMMEDIATE")) for sql in statements)
        counts.append(len(statements))
    assert counts[0] == counts[1]


@pytest.mark.parametrize("change",["digest","time","row","busy","late","evict"])
def test_stale_or_lost_context_no_partial_proposal(service,monkeypatch,change):
    from backend.service.import_pairing_service import ImportPairingService
    current,rows,_accepted = paired(service)
    values = payload(current,{key:choice() for key in rows})
    expected = "PREVIEW_CHANGED"
    if change=="digest":
        values["preview_digest"] = "0"*64
    elif change=="time":
        values["expected_updated_time"] += timedelta(microseconds=1)
    elif change=="row":
        values["choices"][0]["file_id"] = 9999
        expected = "PREVIEW_ROW_NOT_FOUND"
    elif change=="busy":
        with service.store.claim(current["token"],current["updated_time"]):
            with pytest.raises(TargetIntakeError) as error:
                service.pairing_preview(current["token"],ImportPairingPreviewInput(**values))
            assert error.value.code == "PREVIEW_BUSY"
        return
    else:
        original = ImportPairingService.propose
        def changed(instance,state,choices,kind):
            result = original(instance,state,choices,kind)
            if change=="evict":
                service.store.remove(state.token)
            else:
                service.store.replace(state,state.updated_time)
            return result
        monkeypatch.setattr(ImportPairingService,"propose",changed)
        if change=="evict":
            expected = "PREVIEW_UNAVAILABLE"
    with pytest.raises(TargetIntakeError) as error:
        service.pairing_preview(current["token"],ImportPairingPreviewInput(**values))
    assert error.value.code == expected


def test_candidate_budget_and_broken_keeper_not_silently_filtered(service,monkeypatch):
    import backend.mapper.import_batch_mapper as module
    current,rows,accepted = paired(service,3)
    selected = {key:choice() for key in rows}
    before = manifest(service)
    with monkeypatch.context() as patch:
        patch.setattr(module,"MAX_EVIDENCE_CANDIDATES",2)
        with pytest.raises(TargetIntakeError) as error:
            pairing(service,current,selected)
        assert error.value.code == "IMPORT_MATCH_LIMIT"
    assert manifest(service) == before
    service.db.execute(update(LedgerEntry).where(LedgerEntry.id==accepted["processed_rows"][0]["created_ledger_id"]).values(account_ref_id=9999))
    service.db.commit()
    with pytest.raises((TargetIntakeError,TargetEconomicError)) as error:
        pairing(service,current,selected)
    assert error.value.code == "ACCOUNT_RELATION_BROKEN"


def test_twenty_thousand_index_same_and_cross_partition_counts_not_all_pairs():
    values = dict(occurred_time="2024-01-01T00:00:00.000000Z",cash_direction=2,amount=1,currency_code="CNY")
    targets = [dict(identity=("NEW",str(n)),values=values,proof=dict(source_type=201,values=dict(account_code=str(n%2)))) for n in range(20000)]
    index = ExactPairingIndex(targets)
    assert index.match(values,targets[0]["proof"],"SAME_SOURCE",targets[0]["identity"]) == (9999,None)
    assert index.match(values,targets[0]["proof"],"CROSS_SOURCE",targets[0]["identity"]) == (10000,None)
    assert len(index.representatives[next(iter(index.scopes))]) == 2
    assert all(len(bucket[1]) <= 2 for bucket in next(iter(index.scopes.values())).values())
    # Unique other source after many same-source candidates still discoverable.
    other = dict(identity=("FACT",99),values=values,proof=dict(source_type=201,values=dict(account_code="other")))
    index = ExactPairingIndex(targets[::2]+[other])
    assert index.match(values,targets[0]["proof"],"CROSS_SOURCE",targets[0]["identity"]) == (1,other)


def test_strict_complete_selection_input_not_financial_capacity(service):
    current,rows,_ = paired(service)
    values = payload(current,{key:choice() for key in rows})
    for changes in (dict(kind="ANY"),dict(choices=[]),dict(choices=values["choices"]*2),
                    dict(choices=[dict(values["choices"][0],source_row_number=True)])):
        with pytest.raises(ValidationError):
            ImportPairingPreviewInput(**(values | changes))
    base = values["choices"][0]
    full = [base | dict(source_row_number=n) for n in range(1,20001)]
    assert len(ImportPairingPreviewInput(**(values | dict(choices=full))).choices) == 20000
    with pytest.raises(ValidationError):
        ImportPairingPreviewInput(**(values | dict(choices=full+[base | dict(source_row_number=20001)])))


def test_new_reliable_cross_source_business_key_can_be_suggested_not_overwritten(service):
    original = prepare(service.mapper,[row(reference="reliable-A")])
    accepted = accept(service.mapper,original)
    incoming = prepare(service.mapper,[row(reference="reliable-B",**cross_identity())],sha="b"*64)
    current = install(service,incoming,{})
    selected = {key:choice() for key in incoming}
    assert pairing(service,current,selected)["items"][0]["reason_codes"] == ["AUTO_IDENTITY_RETAINED"]
    item = pairing(service,current,selected,"CROSS_SOURCE")["items"][0]
    assert item["state"] == "SUGGESTED" and item["suggestion"]["target"] == dict(kind="FACT",transaction_id=accepted["processed_rows"][0]["transaction_id"])


@pytest.mark.parametrize("kind",["SAME_SOURCE","CROSS_SOURCE"])
def test_explicitly_applied_row_proposal_uses_existing_preview_and_atomic_financial_kernel(service,kind):
    from backend.schema.import_command import ImportReviseInput, ImportConfirmInput
    from test_pirc35_import_confirm_preview import batch, input_for
    a = prepare(service.mapper,[row(reference="")])
    b = prepare(service.mapper,[row(reference="",**(cross_identity() if kind=="CROSS_SOURCE" else {}))],sha="b"*64)
    rows = a | b
    current = install(service,rows,{})
    anchor,source = next(iter(a)),next(iter(b))
    selected = {anchor:choice(resolution="NEW",acknowledge_new_risk=True),source:choice()}
    before = manifest(service)
    suggestion = pairing(service,current,selected,kind)["items"][1]["suggestion"]
    assert manifest(service) == before
    selected[source] |= {field:suggestion[field] for field in ("resolution","target")}
    saved = service.revise(current["token"],ImportReviseInput(expected_updated_time=current["updated_time"],
        choices=payload(current,selected)["choices"]))
    disclosure = batch(service,saved,list(rows))
    assert disclosure["can_confirm"] and disclosure["counts"]["new_real_fact"] == 1
    assert disclosure["counts"]["new_duplicate_fact"] == int(kind=="CROSS_SOURCE")
    assert manifest(service) == before
    result = service.confirm(saved["token"],ImportConfirmInput(**input_for(saved,list(rows),batch_preview_digest=disclosure["batch_preview_digest"])))
    assert result["new_fact_count"] == (2 if kind=="CROSS_SOURCE" else 1)
    anchor_result,source_result = result["processed_rows"]
    if kind=="CROSS_SOURCE":
        assert source_result["duplicate_kept_transaction_id"] == anchor_result["transaction_id"]
        assert source_result["resolution_effect"] == "DUPLICATE_ZERO"
        assert source_result["created_review_id"] not in source_result["effective_review_ids"]
    else:
        assert source_result["transaction_id"] == anchor_result["transaction_id"]
        assert source_result["resolution_effect"] == "EVIDENCE_ONLY" and not source_result["created_review_id"]


@pytest.mark.parametrize("budget",["match","response"])
def test_whole_matching_deadline_or_response_budget_fail_without_a_prefix(service,monkeypatch,budget):
    import backend.mapper.import_batch_mapper as mapper_module
    import backend.service.import_batch_service as service_module
    current,rows,_ = paired(service,3)
    selected = {key:choice() for key in rows}
    before,retained = manifest(service),service.store.get(current["token"])
    if budget=="match":
        started = mapper_module.monotonic()
        original = mapper_module.ImportBatchMapper.signature_facts
        def expired(instance,signatures):
            result = original(instance,signatures)
            monkeypatch.setattr(mapper_module,"monotonic",lambda:started+3)
            return result
        monkeypatch.setattr(mapper_module.ImportBatchMapper,"signature_facts",expired)
    else:
        monkeypatch.setattr(service_module,"canonical_json",lambda _value:"x"*(24*1024*1024+1))
    with pytest.raises((TargetIntakeError,TargetEconomicError)) as error:
        pairing(service,current,selected)
    assert error.value.code == ("IMPORT_MATCH_LIMIT" if budget=="match" else "DETAIL_LIMIT")
    assert manifest(service) == before and service.store.get(current["token"]) == retained


@pytest.mark.parametrize("kind",["SAME_SOURCE","CROSS_SOURCE"])
@pytest.mark.parametrize("masked_side",["source","target"])
def test_masked_source_or_original_proof_is_unknown_not_merged_or_filtered(service,kind,masked_side):
    from test_pirc35_import_match import matches
    weak = dict(account=dict(number="**************3456"),source_account=dict(identity_strength="WEAK"))
    originals = prepare(service.mapper,[row(reference="",**(weak if masked_side=="target" else {}))])
    accept(service.mapper,originals)
    incoming = prepare(service.mapper,[row(reference="",**(weak if masked_side=="source" else {}))],sha="b"*64)
    current = install(service,incoming,{})
    selected = {key:choice() for key in incoming}
    before,retained = manifest(service),service.store.get(current["token"])
    item = pairing(service,current,selected,kind)["items"][0]
    assert item["state"] == "EXCEPTION" and item["suggestion"] is None
    assert item["candidate_count"] == (1 if masked_side=="target" else None)
    assert item["reason_codes"] == ["SOURCE_IDENTITY_REQUIRED"]
    # The existing single-row directory uses the same proof policy and keeps
    # the blocked unknown-origin Fact visible, rather than reporting no match.
    if masked_side=="target":
        directory = matches(service,current,next(iter(incoming)),kind)
        assert directory["total"] == 1 and directory["items"][0]["eligible_actions"] == []
        assert directory["items"][0]["reason_codes"] == ["SOURCE_IDENTITY_REQUIRED"]
    assert manifest(service) == before and service.store.get(current["token"]) == retained


def test_two_equal_masked_bank_sources_cannot_publish_manual_link_even_with_forged_preview(service):
    from backend.schema.import_command import ImportReviseInput, ImportConfirmInput
    from test_pirc35_import_confirm_preview import input_for
    weak = dict(account=dict(number="**************3456"),source_account=dict(identity_strength="WEAK"))
    original = prepare(service.mapper,[row(reference="",**weak)])
    id = accept(service.mapper,original)["processed_rows"][0]["transaction_id"]
    incoming = prepare(service.mapper,[row(reference="",**weak)],sha="b"*64)
    current = install(service,incoming,{})
    key = next(iter(incoming))
    selected = {key:choice(resolution="LINK_EXISTING",target=dict(kind="FACT",transaction_id=id))}
    before = manifest(service)
    with pytest.raises(TargetIntakeError) as error:
        service.revise(current["token"],ImportReviseInput(expected_updated_time=current["updated_time"],choices=payload(current,selected)["choices"]))
    assert error.value.code == "SOURCE_IDENTITY_REQUIRED" and manifest(service) == before
    # Deliberately install a forged draft to exercise the locked command's
    # independent proof check. This is a fixture, not a public bypass API.
    state = service.store.get(current["token"])
    state.choices.update(selected)
    service.store.replace(state,state.updated_time)
    current = service.current(state.token)
    with pytest.raises(TargetIntakeError) as error:
        service.confirm(state.token,ImportConfirmInput(**input_for(current,[key],batch_preview_digest="0"*64)))
    assert error.value.code == "SOURCE_IDENTITY_REQUIRED" and manifest(service) == before


def test_complete_source_proof_accepts_full_digits_not_tail_or_redacted_profile():
    from backend.core.import_evidence import complete_source_code
    assert complete_source_code(201,"0000000000123456")
    assert complete_source_code(101,"fictional-full-profile@example.org")
    for value in ("3456","**************3456","0000000000123456…",""):
        assert not complete_source_code(201,value)
    for value in ("Mock***","Mock＊","Mock•","Mock…"):
        assert not complete_source_code(101,value)
