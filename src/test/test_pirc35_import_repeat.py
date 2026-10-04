"""Explicit suspected repeat-export groups, never automatic source merging."""
from copy import deepcopy
from datetime import timedelta

import pytest

from backend.schema.import_command import ImportRepeatPreviewInput, ImportReviseInput, ImportConfirmInput
from backend.schema.import_batch_read import ImportRepeatPreviewPO
from backend.error import TargetIntakeError
from test_pirc35_import_service import service
from test_pirc35_import_batch import prepare, row, accept
from test_pirc35_import_confirm_preview import install, input_for, batch
from test_pirc35_import_duplicate import manifest
from test_pirc35_import_pairing import choice


def exports(service, count=2, changes=None):
    rows = {}
    for n in range(count):
        rows |= prepare(service.mapper, [row(reference="", **(changes or {}))], sha=str(n+1)*64)
    return install(service, rows, {}), rows


def read(service, current, rows, choices=None):
    choices = choices or {key:choice(decision="SKIP") for key in rows}
    result = service.repeat_preview(current["token"], ImportRepeatPreviewInput(expected_updated_time=current["updated_time"],
        preview_digest=current["preview_digest"], choices=[dict(file_id=key[0], source_row_number=key[1], **value)
            for key,value in choices.items()]))
    ImportRepeatPreviewPO(**result)
    return result


def test_three_exports_propose_first_and_two_skips_without_mutation(service):
    current, rows = exports(service, 3)
    before, cached = manifest(service), deepcopy(service.store.get(current["token"]))
    result = read(service, current, rows)
    assert len(result["groups"]) == 1
    group = result["groups"][0]
    keys = sorted(rows)
    assert group["keeper_row"] == dict(file_id=keys[0][0], source_row_number=1)
    assert group["repeated_rows"] == [dict(file_id=key[0], source_row_number=1) for key in keys[1:]]
    assert group["kind"] == "SUSPECTED_EXPORT"
    assert group["parsed"]["amount"] == 40000 and group["parsed"]["cash_direction"] == "OUT"
    assert all(item["state"] == "GROUPED" for item in result["items"])
    assert manifest(service) == before and service.store.get(current["token"]) == cached
    assert "0000000000123456" not in str(result)


def test_only_later_file_cannot_be_promoted_to_first(service):
    current, rows = exports(service)
    key = sorted(rows)[-1]
    result = read(service,current,{key:rows[key]})
    assert result["groups"] == []
    assert result["items"][0]["reason_codes"] == ["REPEAT_SCOPE_INCOMPLETE"]


def test_same_file_identical_keyless_rows_block_whole_group(service):
    original = prepare(service.mapper,[row(n,reference="") for n in (1,2)],sha="1"*64)
    extra = prepare(service.mapper,[row(reference="")],sha="2"*64)
    current = install(service,original|extra,{})
    result = read(service,current,original|extra)
    assert result["groups"] == []
    assert all(item["reason_codes"] == ["IDENTITY_AMBIGUOUS"] for item in result["items"])


@pytest.mark.parametrize("changes", [dict(amount_minor=40000),dict(amount_minor=-39999),dict(currency="CNY_4"),
    dict(occurred_at="2024-01-01T00:00:00.000001Z"),dict(account=dict(number="9990000000123456"),
        source_account=dict(source_namespace="ccb:statement-v1",source_identity="9990000000123456",identity_strength="RELIABLE"))])
def test_different_core_or_full_source_not_grouped(service,changes):
    first = prepare(service.mapper,[row(reference="")],sha="1"*64)
    second = prepare(service.mapper,[row(reference="",**changes)],sha="2"*64)
    current = install(service,first|second,{})
    assert read(service,current,first|second)["groups"] == []


def test_weak_source_is_exception_not_silently_skipped_in_projection(service):
    current,rows = exports(service,changes=dict(source_account=dict(identity_strength="WEAK")))
    result = read(service,current,rows)
    assert result["groups"] == []
    assert all(item["state"] == "EXCEPTION" and item["reason_codes"] == ["SOURCE_IDENTITY_REQUIRED"] for item in result["items"])


def test_saved_explicit_new_intent_is_retained_and_group_not_partially_applied(service):
    current,rows = exports(service)
    choices = {key:choice(decision="SKIP") for key in rows}
    choices[min(rows)] = choice(resolution="NEW",acknowledge_new_risk=True)
    result = read(service,current,rows,choices)
    assert result["groups"] == []
    assert any(item["reason_codes"] == ["USER_INTENT_RETAINED"] for item in result["items"])


def test_accepted_exact_core_candidate_blocks_new_first_even_for_another_origin(service):
    prior = prepare(service.mapper,[row(reference="")],sha="3"*64)
    accept(service.mapper,prior)
    current,rows = exports(service)
    before = manifest(service)
    result = read(service,current,rows)
    assert result["groups"] == []
    assert all(item["reason_codes"] == ["EXTERNAL_CANDIDATE_REQUIRES_REVIEW"] for item in result["items"])
    assert manifest(service) == before


def test_explicit_group_intent_uses_existing_preflight_and_atomic_writer(service):
    current,rows = exports(service)
    group = read(service,current,rows)["groups"][0]
    first = (group["keeper_row"]["file_id"],group["keeper_row"]["source_row_number"])
    # This is the exact intent the human-confirmed UI must save, not a new
    # financial endpoint or consent granted by the read-only group response.
    current = service.revise(current["token"],ImportReviseInput(expected_updated_time=current["updated_time"],choices=[dict(
        file_id=key[0],source_row_number=key[1],decision="ACCEPT" if key==first else "SKIP",
        resolution="NEW" if key==first else "AUTO",acknowledge_new_risk=key==first) for key in rows]))
    preview = batch(service,current,list(rows))
    assert preview["can_confirm"] and preview["counts"]["new_real_fact"] == preview["counts"]["skipped"] == 1
    result = service.confirm(current["token"],ImportConfirmInput(**input_for(current,list(rows),
        batch_preview_digest=preview["batch_preview_digest"])))
    assert result["new_fact_count"] == result["skipped_count"] == 1
    saved = manifest(service)
    assert len(saved["transaction_fact"]) == len(saved["review_case"]) == len(saved["ledger_entry"]) == 1
    assert len(saved["transaction_import_row"]) == 2
    assert all(item["raw_payload"] for item in saved["transaction_import_row"])


def test_repeat_preview_rejects_stale_and_unknown_rows_without_writes(service):
    current,rows = exports(service)
    before = manifest(service)
    with pytest.raises(TargetIntakeError) as error:
        read(service,current|dict(updated_time=current["updated_time"]-timedelta(microseconds=1)),rows)
    assert error.value.code == "PREVIEW_CHANGED"
    with pytest.raises(TargetIntakeError) as error:
        read(service,current,{(999,1):row()})
    assert error.value.code == "PREVIEW_ROW_NOT_FOUND"
    assert manifest(service) == before
