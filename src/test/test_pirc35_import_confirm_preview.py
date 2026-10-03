"""Complete import disclosure and locked guards, using fictional databases."""
from copy import deepcopy
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select, update

from backend.core.import_preview_store import ImportPreviewState
from backend.entity import (TransactionFact, TransactionImportRow, TransactionImportFile, ReviewCase,
                            LedgerEntry, ReviewAllocation, LedgerAccountRef, TargetTag, TargetTagView)
from backend.error import TargetEconomicError, TargetIntakeError
from backend.schema.import_command import ImportConfirmInput, ImportConfirmPreviewInput
from backend.schema.import_batch_read import ImportBatchPreviewPO
from backend.service.import_batch_service import ImportBatchService
from test_pirc35_import_service import service, preview, choose
from test_pirc35_import_batch import prepare, row, accept, count
from test_pirc35_review_command import execute, normal
from test_pirc35_import_api import client


def input_for(current, keys, **extra):
    return dict(expected_updated_time=current["updated_time"], preview_digest=current["preview_digest"],
                selected_rows=[dict(file_id=key[0], source_row_number=key[1]) for key in keys], **extra)


def batch(service, current, keys):
    return service.confirm_preview(current["token"], ImportConfirmPreviewInput(**input_for(current, keys)))


def install(service, rows, choices):
    files = service.mapper.rows(TransactionImportFile, TransactionImportFile.id, {key[0] for key in rows})
    state = ImportPreviewState(token="fictional-confirm-plan", rows=deepcopy(rows), choices=deepcopy(choices),
        files=[dict(file_id=file["id"], sha256=file["sha256"], filename=file["filename"],
                    parsed_row_count=file["total_count"], error=None) for file in files])
    ImportBatchService.group_premises(state)
    state.candidates = service.mapper.match(state.rows, state.choices)
    service.db.rollback()
    service.store.put(state)
    return service.current(state.token)


def manual_fact(service, *, group=False):
    original = prepare(service.mapper, [row(n, reference="") for n in range(1, 3 if group else 2)])
    result = accept(service.mapper, original)
    ids = [value["transaction_id"] for value in result["processed_rows"]]
    if group:
        execute(service.db, new_reviews=[normal(*ids)])
    rows = prepare(service.mapper, [row(reference="")], sha="b" * 64)
    key = next(iter(rows))
    current = install(service, rows, {key: dict(decision="ACCEPT", resolution="LINK_EXISTING",
        target=dict(kind="FACT", transaction_id=ids[0]))})
    return current, [key], ids


def snapshot(service):
    return {entity: [dict(value) for value in service.db.execute(select(entity.__table__).order_by(entity.id)).mappings()]
            for entity in (TransactionFact, TransactionImportRow, ReviewCase, LedgerEntry, ReviewAllocation, LedgerAccountRef)}


def test_read_preview_discloses_exact_new_defaults_without_writing_or_guessing_ids(service):
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:2]
    current = choose(service, current, keys)
    before = snapshot(service)
    result = batch(service, current, keys)
    ImportBatchPreviewPO(**result)
    assert result["can_confirm"] and result["issues"] == []
    assert result["counts"] == dict(new_real_fact=2, new_duplicate_fact=0, evidence_only=0, skipped=0, invalid=0, unresolved=0)
    assert result["expected_reviews"] == result["effects"]["before_after_review_states"] == []
    assert [value["row"] for value in result["effects"]["new_original_defaults"]] == result["selected_rows"]
    assert [value["output_index"] for value in result["effects"]["new_original_defaults"]] == [0, 1]
    assert all("id" not in value and "transaction_id" not in value for value in result["effects"]["new_original_defaults"])
    assert result["budget"]["selected_rows"] == result["budget"]["outputs"] == result["budget"]["position_links"] == 2
    assert snapshot(service) == before
    assert service.store.get(current["token"]).updated_time == current["updated_time"]


def test_batch_digest_is_order_independent_and_distinct_from_whole_source_digest(service):
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:3]
    current = choose(service, current, keys)
    first = batch(service, current, keys)
    second = batch(service, current, list(reversed(keys)))
    subset = batch(service, current, keys[:2])
    assert first == second
    assert first["source_preview_digest"] == current["preview_digest"]
    assert first["batch_preview_digest"] != current["preview_digest"]
    assert subset["batch_preview_digest"] != first["batch_preview_digest"]


def test_supplied_batch_digest_confirms_actual_output_ids(service):
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:2]
    current = choose(service, current, keys)
    disclosure = batch(service, current, keys)
    result = service.confirm(current["token"], ImportConfirmInput(**input_for(current, keys,
        batch_preview_digest=disclosure["batch_preview_digest"])))
    assert result["new_fact_count"] == 2
    assert all(value["created_review_id"] in value["effective_review_ids"] for value in result["processed_rows"])
    assert all(value["created_ledger_id"] in value["effective_ledger_ids"] for value in result["processed_rows"])


def test_missing_choices_and_invalid_selected_rows_are_complete_blocking_disclosure(service):
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:3]
    result = batch(service, current, keys)
    assert not result["can_confirm"] and result["counts"]["unresolved"] == 3
    assert len(result["issues"]) == len(result["pairs"]) == 3
    assert {value["code"] for value in result["issues"]} == {"ROW_CHOICE_REQUIRED"}
    assert result["effects"]["new_original_defaults"] == []
    assert count(service.mapper, TransactionFact) == 0


def test_selection_or_digest_change_is_refused_before_financial_write(service):
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:2]
    current = choose(service, current, keys)
    disclosure = batch(service, current, keys)
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        service.confirm(current["token"], ImportConfirmInput(**input_for(current, keys[:1],
            batch_preview_digest=disclosure["batch_preview_digest"])))
    assert count(service.mapper, TransactionFact) == 0
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        service.confirm(current["token"], ImportConfirmInput(**input_for(current, keys,
            batch_preview_digest=current["preview_digest"])))
    assert count(service.mapper, TransactionImportRow) == 0


def test_manual_existing_pair_discloses_original_and_current_outputs_and_no_new_cash(service):
    current, keys, ids = manual_fact(service)
    before = snapshot(service)
    disclosure = batch(service, current, keys)
    ImportBatchPreviewPO(**disclosure)
    assert disclosure["counts"]["evidence_only"] == 1 and disclosure["counts"]["new_real_fact"] == 0
    assert disclosure["effects"]["by_currency"] == disclosure["effects"]["new_original_defaults"] == []
    assert disclosure["effects"]["tag_effect"]["projected_assignment_count"] == 0
    states = disclosure["effects"]["before_after_review_states"]
    assert len(states) == len(disclosure["expected_reviews"]) == 1
    assert states[0]["before"]["allocations"][0]["transaction_id"] == ids[0]
    assert len(states[0]["before"]["ledger_entries"]) == 1
    assert "0000000000123456" not in str(disclosure)
    assert snapshot(service) == before
    result = service.confirm(current["token"], ImportConfirmInput(**input_for(current, keys,
        batch_preview_digest=disclosure["batch_preview_digest"])))
    assert result["manual_linked_count"] == result["linked_existing_count"] == 1
    assert result["new_fact_count"] == 0
    assert result["processed_rows"][0]["transaction_id"] == ids[0]


def test_manual_target_needs_batch_digest_not_just_source_digest(service):
    current, keys, _ids = manual_fact(service)
    before = snapshot(service)
    with pytest.raises(TargetIntakeError, match="IMPORT_REVIEW_REQUIRED"):
        service.confirm(current["token"], ImportConfirmInput(**input_for(current, keys)))
    assert snapshot(service) == before


@pytest.mark.parametrize("field,value", [("status", 1), ("title", "changed explanation metadata")])
def test_target_review_change_with_same_time_invalidates_locked_disclosure(service, field, value):
    current, keys, _ids = manual_fact(service)
    disclosure = batch(service, current, keys)
    review_id = disclosure["expected_reviews"][0]["review_id"]
    service.db.execute(update(ReviewCase).where(ReviewCase.id == review_id).values(**{field: value}))
    service.db.commit()
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        service.confirm(current["token"], ImportConfirmInput(**input_for(current, keys,
            batch_preview_digest=disclosure["batch_preview_digest"])))
    assert count(service.mapper, TransactionImportRow) == count(service.mapper, TransactionFact) == 1
    refreshed = batch(service, current, keys)
    assert refreshed["batch_preview_digest"] != disclosure["batch_preview_digest"]


def test_shared_target_group_is_complete_and_collateral_state_is_bound(service):
    current, keys, ids = manual_fact(service, group=True)
    disclosure = batch(service, current, keys)
    ImportBatchPreviewPO(**disclosure)
    states = disclosure["effects"]["before_after_review_states"]
    assert len(states) == 2  # original A default plus its current complete group
    active = next(value["before"] for value in states if value["after_status"] == "CONFIRMED")
    assert {value["transaction_id"] for value in active["allocations"]} == set(ids)
    assert len(active["ledger_entries"]) == 2
    assert disclosure["budget"]["facts"] == 2 and disclosure["budget"]["outputs"] == 3
    service.db.execute(update(TransactionFact).where(TransactionFact.id == ids[1]).values(summary="changed collateral metadata"))
    service.db.commit()
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        service.confirm(current["token"], ImportConfirmInput(**input_for(current, keys,
            batch_preview_digest=disclosure["batch_preview_digest"])))
    assert count(service.mapper, TransactionImportRow) == 2


def test_unrelated_existing_review_does_not_force_whole_preview_rescan(service):
    original = prepare(service.mapper, [row(n, reference="") for n in (1, 2)])
    imported = accept(service.mapper, original)
    ids = [value["transaction_id"] for value in imported["processed_rows"]]
    rows = prepare(service.mapper, [row(reference="")], sha="b" * 64)
    key = next(iter(rows))
    current = install(service, rows, {key: dict(decision="ACCEPT", resolution="LINK_EXISTING",
        target=dict(kind="FACT", transaction_id=ids[0]))})
    disclosure = batch(service, current, [key])
    unrelated = imported["processed_rows"][1]["created_review_id"]
    service.db.execute(update(ReviewCase).where(ReviewCase.id == unrelated).values(title="unrelated metadata"))
    service.db.commit()
    assert batch(service, current, [key])["batch_preview_digest"] == disclosure["batch_preview_digest"]


def add_view(service):
    view = TargetTagView(system_name="fictional-view", name="Mock view", status="ACTIVE")
    service.db.add(view)
    service.db.flush()
    default = TargetTag(view_id=view.id, system_name="unclassified", name="Mock default", status="ACTIVE")
    other = TargetTag(view_id=view.id, system_name="other", name="Mock other", status="ACTIVE")
    service.db.add_all([default, other])
    service.db.commit()
    return view.id, default.id, other.id


def test_default_tag_premises_are_disclosed_and_revalidated_under_write_lock(service):
    _view, default, _other = add_view(service)
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:2]
    current = choose(service, current, keys)
    disclosure = batch(service, current, keys)
    effect = disclosure["effects"]["tag_effect"]
    assert effect["projected_assignment_count"] == disclosure["budget"]["tag_changes"] == 2
    assert effect["default_assignments"][0]["tag_id"] == default
    service.db.execute(update(TargetTag).where(TargetTag.id == default).values(name="different default metadata"))
    service.db.commit()
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        service.confirm(current["token"], ImportConfirmInput(**input_for(current, keys,
            batch_preview_digest=disclosure["batch_preview_digest"])))
    assert count(service.mapper, TransactionFact) == 0


def test_unused_tag_metadata_is_not_an_unrelated_stale_premise(service):
    _view, _default, other = add_view(service)
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:1]
    current = choose(service, current, keys)
    disclosure = batch(service, current, keys)
    service.db.execute(update(TargetTag).where(TargetTag.id == other).values(name="unrelated tag name"))
    service.db.commit()
    assert batch(service, current, keys)["batch_preview_digest"] == disclosure["batch_preview_digest"]


def test_row_anchor_plan_is_explicit_and_confirm_maps_actual_ids(service):
    anchors = prepare(service.mapper, [row(reference="")])
    sources = prepare(service.mapper, [row(reference="")], sha="b" * 64)
    anchor, source = next(iter(anchors)), next(iter(sources))
    rows = anchors | sources
    current = install(service, rows, {anchor: dict(decision="ACCEPT", resolution="NEW"),
        source: dict(decision="ACCEPT", resolution="LINK_EXISTING",
                     target=dict(kind="ROW", file_id=anchor[0], source_row_number=anchor[1]))})
    disclosure = batch(service, current, list(rows))
    ImportBatchPreviewPO(**disclosure)
    assert disclosure["counts"]["new_real_fact"] == disclosure["counts"]["evidence_only"] == 1
    assert len(disclosure["effects"]["new_original_defaults"]) == 1
    assert disclosure["pairs"][1]["target"] == dict(kind="ROW", file_id=anchor[0], source_row_number=anchor[1])
    result = service.confirm(current["token"], ImportConfirmInput(**input_for(current, list(rows),
        batch_preview_digest=disclosure["batch_preview_digest"])))
    assert len({value["transaction_id"] for value in result["processed_rows"]}) == 1
    assert result["new_fact_count"] == result["manual_linked_count"] == 1


def test_reliable_source_group_has_one_cash_projection_and_extra_evidence_count(service):
    rows = prepare(service.mapper, [row(1), row(2)])
    current = install(service, rows, {key: dict(decision="ACCEPT") for key in rows})
    disclosure = batch(service, current, list(rows))
    assert disclosure["counts"]["new_real_fact"] == disclosure["counts"]["evidence_only"] == 1
    assert disclosure["effects"]["by_currency"][0]["cash_out_amount"] == 40000
    assert len(disclosure["effects"]["new_original_defaults"]) == 1


def test_response_budget_refuses_whole_plan_instead_of_silent_truncation(service, monkeypatch):
    import backend.service.import_confirmation_service as module
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:2]
    current = choose(service, current, keys)
    monkeypatch.setattr(module, "MAX_BATCH_PREVIEW_BYTES", 100)
    with pytest.raises(TargetIntakeError, match="DETAIL_LIMIT"):
        batch(service, current, keys)
    assert count(service.mapper, TransactionFact) == 0


def test_target_broken_reference_is_error_not_missing_eligible_candidate(service):
    current, keys, _ids = manual_fact(service)
    service.db.execute(update(LedgerEntry).values(account_ref_id=99999))
    service.db.commit()
    with pytest.raises(TargetEconomicError) as error:
        batch(service, current, keys)
    assert error.value.code == "ACCOUNT_RELATION_BROKEN"
    assert count(service.mapper, TransactionImportRow) == 1


def test_unknown_after_commit_does_not_erase_evidence_or_generate_default_again(service):
    current, keys, ids = manual_fact(service)
    disclosure = batch(service, current, keys)
    def fault(stage):
        if stage == "after_commit":
            raise RuntimeError("fictional lost response")
    with pytest.raises(TargetIntakeError, match="RESULT_UNKNOWN"):
        service.confirm(current["token"], ImportConfirmInput(**input_for(current, keys,
            batch_preview_digest=disclosure["batch_preview_digest"])), fault=fault)
    stored = service.mapper.source_rows(keys)[keys[0]]
    assert stored["row_status"] == 1 and stored["transaction_fact_id"] == ids[0]
    assert count(service.mapper, TransactionFact) == count(service.mapper, LedgerEntry) == 1
    with pytest.raises(TargetIntakeError, match="ROWS_ALREADY_PROCESSED"):
        service.confirm(current["token"], ImportConfirmInput(**input_for(current, keys,
            batch_preview_digest=disclosure["batch_preview_digest"])))


def test_read_snapshot_and_complete_batch_are_not_changed_by_preview_claim(service):
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:1]
    current = choose(service, current, keys)
    with service.store.claim(current["token"], current["updated_time"]):
        with pytest.raises(TargetIntakeError, match="PREVIEW_BUSY"):
            batch(service, current, keys)
    assert batch(service, current, keys)["can_confirm"]


def test_concurrent_choice_change_invalidates_read_disclosure_before_return(service, monkeypatch):
    from backend.service.import_confirmation_service import ImportConfirmationService
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:1]
    current = choose(service, current, keys)
    original = ImportConfirmationService.plan
    def concurrent(self, state, order, candidates, digest):
        result = original(self, state, order, candidates, digest)
        changed = service.store.get(current["token"])
        changed.choices[keys[0]]["decision"] = "SKIP"
        service.store.replace(changed, changed.updated_time)
        return result
    monkeypatch.setattr(ImportConfirmationService, "plan", concurrent)
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        batch(service, current, keys)
    assert count(service.mapper, TransactionFact) == 0


def test_preflight_selection_contract_keeps_per_transaction_bounds_and_strict_digest():
    base = dict(expected_updated_time="2024-01-01T00:00:00Z", preview_digest="a" * 64,
                selected_rows=[dict(file_id=1, source_row_number=1)])
    for changed in (dict(selected_rows=base["selected_rows"] * 1001), dict(selected_rows=[]),
                    dict(expected_updated_time="2024-01-01T00:00:00"), dict(batch_preview_digest="not valid")):
        with pytest.raises(ValidationError):
            ImportConfirmInput(**(base | changed))


def test_canonical_new_anchor_and_supplement_effect_match_reversed_confirm_order(service):
    rows = prepare(service.mapper, [row(1, note="canonical"), row(2, note="supplement")])
    keys = sorted(rows)
    current = install(service, rows, {key: dict(decision="ACCEPT") for key in keys})
    disclosure = batch(service, current, list(reversed(keys)))
    result = service.confirm(current["token"], ImportConfirmInput(**input_for(current, list(reversed(keys)),
        batch_preview_digest=disclosure["batch_preview_digest"])))
    outcomes = {(value["file_id"], value["source_row_number"]): value for value in result["processed_rows"]}
    assert outcomes[keys[0]]["resolution_effect"] == "NEW_REAL"
    assert outcomes[keys[1]]["resolution_effect"] == "EVIDENCE_ONLY"
    assert service.db.scalar(select(TransactionFact.summary)) == "canonical"
    assert disclosure["effects"]["new_original_defaults"][0]["row"] == dict(file_id=keys[0][0], source_row_number=1)


def test_pure_import_thousand_defaults_does_not_inherit_review_hundred_group_limit(service):
    rows = prepare(service.mapper, [row(n, reference=f"fictional-{n}") for n in range(1, 1001)])
    current = install(service, rows, {key: dict(decision="ACCEPT") for key in rows})
    disclosure = batch(service, current, list(rows))
    ImportBatchPreviewPO(**disclosure)
    assert disclosure["can_confirm"] and disclosure["budget"]["review_groups"] == 1000
    assert len(disclosure["effects"]["new_original_defaults"]) == 1000
    assert disclosure["effects"]["by_currency"][0]["cash_out_amount"] == 40_000_000
    assert count(service.mapper, TransactionFact) == 0


def test_complete_existing_context_is_batched_for_one_and_hundred_targets(service):
    original = prepare(service.mapper, [row(n, reference="") for n in range(1, 101)])
    imported = accept(service.mapper, original)
    ids = [value["transaction_id"] for value in imported["processed_rows"]]
    rows = prepare(service.mapper, [row(n, reference="") for n in range(1, 101)], sha="b" * 64)
    keys = sorted(rows)
    current = install(service, rows, {key: dict(decision="ACCEPT", resolution="LINK_EXISTING",
        target=dict(kind="FACT", transaction_id=id)) for key, id in zip(keys, ids)})
    counts = []
    for selected in (keys[:1], keys):
        statements = []
        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)
        event.listen(service.db.bind, "before_cursor_execute", capture)
        try:
            disclosure = batch(service, current, selected)
        finally:
            event.remove(service.db.bind, "before_cursor_execute", capture)
        assert len(disclosure["expected_reviews"]) == len(selected)
        assert all(not statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for statement in statements)
        counts.append(sum(statement.lstrip().upper().startswith("SELECT") for statement in statements))
    assert counts[0] == counts[1]


def test_preflight_current_and_original_groups_share_one_wal_snapshot(service):
    current, keys, _ids = manual_fact(service)
    service.db.rollback()
    with service.db.bind.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    changed = []
    def change_review(_connection, _cursor, statement, _parameters, _context, _many):
        if not changed and "review_transaction_ledger_allocation" in statement and "review_case.behavior_type =" in statement:
            changed.append(True)
            with service.db.bind.begin() as concurrent:
                concurrent.execute(update(ReviewCase).values(status=1))
    event.listen(service.db.bind, "after_cursor_execute", change_review)
    try:
        disclosure = batch(service, current, keys)
    finally:
        event.remove(service.db.bind, "after_cursor_execute", change_review)
    assert changed and disclosure["expected_reviews"][0]["status"] == "CONFIRMED"
    assert disclosure["effects"]["before_after_review_states"][0]["before"]["status"] == "CONFIRMED"
    assert service.db.scalar(select(ReviewCase.status)) == 1
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        service.confirm(current["token"], ImportConfirmInput(**input_for(current, keys,
            batch_preview_digest=disclosure["batch_preview_digest"])))


def test_tag_impact_budget_is_checked_before_building_or_writing_partial_effects(service, monkeypatch):
    from backend.mapper.target_tag_projection_mapper import ActiveTagValue, TargetTagProjectionMapper
    rows = prepare(service.mapper, [row(n, reference="") for n in range(1, 101)])
    current = install(service, rows, {key: dict(decision="ACCEPT") for key in rows})
    monkeypatch.setattr(TargetTagProjectionMapper, "active_dictionary", lambda _self:
        tuple(ActiveTagValue(n, f"view-{n}", n, "unclassified") for n in range(1, 502)))
    with pytest.raises(TargetIntakeError, match="TAG_IMPACT_LIMIT"):
        batch(service, current, list(rows))
    assert count(service.mapper, TransactionFact) == 0


@pytest.mark.parametrize("field,value", [("name", "changed source card name"),
    ("source_identity", "different reliable source identity")])
def test_current_source_card_metadata_changes_are_in_the_batch_guard(service, field, value):
    current, keys, _ids = manual_fact(service)
    disclosure = batch(service, current, keys)
    service.db.execute(update(LedgerAccountRef).values(**{field: value}))
    service.db.commit()
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        service.confirm(current["token"], ImportConfirmInput(**input_for(current, keys,
            batch_preview_digest=disclosure["batch_preview_digest"])))
    assert count(service.mapper, TransactionImportRow) == 1


def test_new_endpoint_and_openapi_are_typed_and_read_only(client):
    from test_pirc35_import_api import preview as api_preview, page as api_page, BASE, selected
    current = api_preview(client)
    rows = api_page(client, current)["items"][:2]
    response = client.put(BASE + f"/preview/{current['token']}", json=dict(expected_updated_time=current["updated_time"],
        choices=[selected(value) | dict(decision="ACCEPT") for value in rows]))
    assert response.status_code == 200
    current = response.json()["body"]
    payload = dict(expected_updated_time=current["updated_time"], preview_digest=current["preview_digest"],
                   selected_rows=[selected(value) for value in rows])
    result = client.post(BASE + f"/preview/{current['token']}/confirm-preview", json=payload)
    assert result.status_code == 200 and result.json()["status"] == 200 and result.json()["message"] == "ok"
    disclosure = result.json()["body"]
    ImportBatchPreviewPO(**disclosure)
    from backend.core import target_database
    with target_database.SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(TransactionFact)) == 0
    openapi = client.get("/openapi.json").json()
    path = openapi["paths"][BASE + "/preview/{token}/confirm-preview"]["post"]
    assert path["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("ImportConfirmPreviewInput")
    assert "ImportBatchPreviewResponse" in str(path["responses"]["200"])
    payload["batch_preview_digest"] = disclosure["batch_preview_digest"]
    confirmed = client.post(BASE + f"/preview/{current['token']}/confirm", json=payload)
    assert confirmed.status_code == 200 and confirmed.json()["body"]["new_fact_count"] == 2


def test_currency_effects_are_exact_separate_units_not_cross_currency_totals(service):
    rows = prepare(service.mapper, [row(1, reference="", amount_minor=123),
                                    row(2, reference="", currency="CNY_4", amount_minor=-456)])
    current = install(service, rows, {key: dict(decision="ACCEPT") for key in rows})
    disclosure = batch(service, current, list(rows))
    assert disclosure["effects"]["by_currency"] == [
        dict(currency_code="CNY", cash_in_amount=123, cash_out_amount=0, excluded_in_amount=0, excluded_out_amount=0),
        dict(currency_code="CNY_4", cash_in_amount=0, cash_out_amount=456, excluded_in_amount=0, excluded_out_amount=0)]


def test_missing_original_default_is_not_repaired_to_make_evidence_plan_confirmable(service):
    current, keys, _ids = manual_fact(service)
    service.db.execute(update(ReviewCase).values(behavior_type=4))
    service.db.commit()
    with pytest.raises(TargetEconomicError) as error:
        batch(service, current, keys)
    assert error.value.code == "DEFAULT_IDENTITY_REQUIRED"
    assert count(service.mapper, TransactionImportRow) == count(service.mapper, ReviewCase) == 1


def test_cancellation_during_read_returns_gone_not_unknown_financial_commit(service, monkeypatch):
    from backend.service.import_confirmation_service import ImportConfirmationService
    current = preview(service)
    keys = sorted(service.store.get(current["token"]).rows)[:1]
    current = choose(service, current, keys)
    original = ImportConfirmationService.plan
    def cancel(self, state, order, candidates, digest):
        result = original(self, state, order, candidates, digest)
        service.store.remove(current["token"])
        return result
    monkeypatch.setattr(ImportConfirmationService, "plan", cancel)
    with pytest.raises(TargetIntakeError) as error:
        batch(service, current, keys)
    assert error.value.status_code == 410 and error.value.code != "RESULT_UNKNOWN"
    assert count(service.mapper, TransactionFact) == 0
