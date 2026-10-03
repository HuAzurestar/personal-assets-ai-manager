"""Actual once-approved serial financial writes in isolated fictional SQLite."""
from datetime import timedelta
from time import monotonic

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select, update

from backend.core.import_preview_store import import_preview_store
from backend.entity import (TransactionFact, TransactionImportRow, ReviewCase, LedgerEntry, ReviewAllocation, LedgerAccountRef,
                            TargetTagView, TargetTag, AutoTagRule)
from backend.error import TargetIntakeError
from backend.entity.base import utc_now
from backend.schema.import_command import (ImportOperationApproveInput, ImportOperationConfirmInput,
    ImportOperationStopInput, ImportConfirmInput, ImportReviseInput)
from backend.schema.import_batch_read import ImportOperationApprovalPO, ImportOperationConfirmPO
from test_pirc35_import_service import service
from test_pirc35_import_api import client, BASE
from test_pirc35_import_operation import independent, operation
from test_pirc35_import_confirm_preview import input_for, install
from test_pirc35_import_duplicate import many_local_pairs, manifest
from test_pirc35_import_batch import prepare, row, accept


def approve(service, current, keys):
    disclosure = operation(service, current, keys)
    result = service.approve_operation(current["token"], ImportOperationApproveInput(**input_for(current, keys,
        operation_preview_digest=disclosure["operation_preview_digest"])))
    ImportOperationApprovalPO(**result)
    return disclosure, result


def child_input(disclosure, current, index):
    child = disclosure["batches"][index]["preview"]
    return ImportOperationConfirmInput(expected_updated_time=current.get("updated_time", current.get("preview_updated_time")),
        preview_digest=current["preview_digest"], selected_rows=child["selected_rows"],
        batch_preview_digest=child["batch_preview_digest"], batch_index=index,
        operation_preview_digest=disclosure["operation_preview_digest"])


def commit_child(service, disclosure, current, index, **extra):
    result = service.confirm(current.get("token", "fictional-confirm-plan"), child_input(disclosure, current, index), **extra)
    ImportOperationConfirmPO(**result)
    return result


def pairs(service):
    rows, choices = many_local_pairs(service, 50)
    return rows, install(service, rows, choices)


def tagged(service):
    service.db.add(TargetTagView(id=1, name="Mock view", system_name="mock-view", status="ACTIVE"))
    service.db.add(TargetTag(id=1, view_id=1, name="Mock default", system_name="unclassified", status="ACTIVE"))
    service.db.add(AutoTagRule(id=1, name="Mock disabled rule", view_id=1, method=1,
        method_config_json='{"schema_version":1,"model_id":9,"prompt":"Mock"}',
        enabled=0, cron="", amount_mode=1, scan_after_ledger_id=100, scan_epoch=1, rule_revision=1))
    service.db.commit()


def test_approval_freezes_whole_plan_without_financial_write_or_result_cache(service):
    rows, current = independent(service, 1001)
    before = manifest(service)
    disclosure, approved = approve(service, current, list(rows))
    assert manifest(service) == before
    assert approved["batch_count"] == 2 and approved["selected_count"] == 1001
    assert approved["updated_time"] > current["updated_time"]
    assert approved["preview_digest"] == current["preview_digest"]
    frozen = service.store.get(current["token"]).operation
    assert [len(child["rows"]) for child in frozen["batches"]] == [1000, 1]
    assert frozen["digest"] == disclosure["operation_preview_digest"] and frozen["next"] == 0
    assert not any(key in frozen for key in ("result", "processed_rows", "effects", "raw_payload"))


def test_actual_2500_commits_1000_1000_500_under_one_complete_approval(service):
    rows, current = independent(service, 2500)
    disclosure, latest = approve(service, current, list(rows))
    results = []
    for index in range(3):
        service.db.rollback()
        began, timings, statements = monotonic(), [], []
        def collect(_connection, _cursor, sql, _parameters, _context, _many):
            if sql.lstrip().upper().startswith("INSERT"):
                statements.append(sql)
        event.listen(service.db.bind, "before_cursor_execute", collect)
        try:
            latest = commit_child(service, disclosure, latest, index,
                fault=lambda stage: timings.append((stage, round(monotonic() - began, 3))))
        finally:
            event.remove(service.db.bind, "before_cursor_execute", collect)
            print("serial child timing", index, timings)
        assert len(statements) <= 40, "default ID collection must not generate one INSERT per row"
        results.append(latest)
        assert latest["next_batch_index"] == index + 1
        assert latest["complete"] == (index == 2)
        assert service.db.scalar(select(func.count()).select_from(TransactionFact)) == min((index + 1) * 1000, 2500)
    assert [len(result["processed_rows"]) for result in results] == [1000, 1000, 500]
    assert sum(result["new_fact_count"] for result in results) == 2500
    assert service.db.scalar(select(func.count()).select_from(ReviewCase)) == 2500
    assert service.db.scalar(select(func.count()).select_from(LedgerEntry)) == 2500
    assert service.db.scalar(select(func.count()).select_from(LedgerAccountRef)) == 1
    assert results[-1]["remaining_count"] == 0
    assert service.store.get(current["token"]).operation is None
    assert len({row["transaction_id"] for result in results for row in result["processed_rows"]}) == 2500


def test_two_compound_children_preserve_row_anchors_and_rebase_only_own_refs_and_rule_rewinds(service):
    tagged(service)
    rows, current = pairs(service)
    disclosure, approved = approve(service, current, list(rows))
    assert [len(item["preview"]["selected_rows"]) for item in disclosure["batches"]] == [98, 2]
    first = commit_child(service, disclosure, approved, 0)
    service.db.rollback()
    second = commit_child(service, disclosure, first, 1)
    assert first["duplicate_fact_count"] == 49 and second["duplicate_fact_count"] == 1
    assert second["complete"] and second["remaining_count"] == 0
    assert service.db.scalar(select(AutoTagRule.scan_epoch)) == 3
    assert service.db.scalar(select(func.count()).select_from(TransactionFact)) == 100
    assert service.db.scalar(select(func.count()).select_from(ReviewCase).where(ReviewCase.status == 0)) == 52
    assert service.db.scalar(select(func.count()).select_from(LedgerAccountRef)) == 2


@pytest.mark.parametrize("change", ["name", "status", "updated_time", "rule", "default_tag"])
def test_external_change_after_first_child_stops_without_second_write(service, change):
    tagged(service)
    rows, current = pairs(service)
    disclosure, approved = approve(service, current, list(rows))
    first = commit_child(service, disclosure, approved, 0)
    if change in {"name", "status", "updated_time"}:
        ref = service.db.scalar(select(LedgerAccountRef).order_by(LedgerAccountRef.id))
        values = {change: "Mock renamed" if change == "name" else "CLOSED" if change == "status" else ref.updated_time + timedelta(microseconds=1)}
        service.db.execute(update(LedgerAccountRef).where(LedgerAccountRef.id == ref.id).values(**values))
    elif change == "rule":
        service.db.execute(update(AutoTagRule).values(scan_epoch=AutoTagRule.scan_epoch + 1))
    else:
        service.db.execute(update(TargetTag).values(name="Mock changed default"))
    service.db.commit()
    before = manifest(service)
    with pytest.raises(TargetIntakeError) as error:
        commit_child(service, disclosure, first, 1)
    assert error.value.code in {"STALE_PREVIEW", "ACCOUNT_NOT_ACTIVE", "INVALID_EVIDENCE_TARGET"}
    assert manifest(service) == before
    assert service.store.get(current["token"]).operation["stopped"]


@pytest.mark.parametrize("stage", ["facts", "defaults", "source_rows", "before_commit", "after_commit"])
def test_child_failure_retains_prior_commit_and_revokes_future_approval(service, stage):
    rows, current = pairs(service)
    disclosure, approved = approve(service, current, list(rows))
    first = commit_child(service, disclosure, approved, 0)
    before = manifest(service)
    def fault(actual):
        if actual == stage:
            raise RuntimeError("Mock child failure")
    with pytest.raises((TargetIntakeError, RuntimeError)) as error:
        commit_child(service, disclosure, first, 1, fault=fault)
    if stage == "after_commit":
        assert error.value.code == "RESULT_UNKNOWN"
        assert service.db.scalar(select(func.count()).select_from(TransactionFact)) == 100
    else:
        assert manifest(service) == before
    assert service.store.get(current["token"]).operation["stopped"]
    durable = manifest(service)
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        commit_child(service, disclosure, first, 1)
    assert manifest(service) == durable


@pytest.mark.parametrize("wrong", ["index", "scope", "batch_digest", "time"])
def test_rejected_child_cannot_leave_an_active_automatic_continuation(service, wrong):
    rows, current = pairs(service)
    disclosure, approved = approve(service, current, list(rows))
    payload = child_input(disclosure, approved, 0)
    if wrong == "index":
        payload.batch_index = 1
    elif wrong == "scope":
        payload.selected_rows = payload.selected_rows[:-1]
    elif wrong == "batch_digest":
        payload.batch_preview_digest = "0" * 64
    else:
        payload.expected_updated_time -= timedelta(microseconds=1)
    before = manifest(service)
    with pytest.raises(TargetIntakeError) as error:
        service.confirm(current["token"], payload)
    assert error.value.code == "STALE_PREVIEW"
    assert manifest(service) == before and service.store.get(current["token"]).operation["stopped"]


def test_stop_during_inflight_commit_survives_lease_publish_and_preserves_remaining(service):
    rows, current = pairs(service)
    disclosure, approved = approve(service, current, list(rows))
    stop = ImportOperationStopInput(operation_preview_digest=disclosure["operation_preview_digest"])
    def fault(stage):
        if stage == "before_commit":
            assert service.stop_operation(current["token"], stop) == dict(stopped=True)
    first = commit_child(service, disclosure, approved, 0, fault=fault)
    assert len(first["processed_rows"]) == 98 and not first["complete"]
    assert service.store.get(current["token"]).operation is None
    assert service.db.scalar(select(func.count()).select_from(TransactionImportRow).where(TransactionImportRow.row_status == 1)) == 98
    before = manifest(service)
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        commit_child(service, disclosure, first, 1)
    assert manifest(service) == before


def test_new_approval_requires_explicit_remaining_refresh_not_old_plan_replay(service):
    rows, current = pairs(service)
    disclosure, approved = approve(service, current, list(rows))
    first = commit_child(service, disclosure, approved, 0)
    service.stop_operation(current["token"], ImportOperationStopInput(operation_preview_digest=disclosure["operation_preview_digest"]))
    latest = service.current(current["token"])
    remaining = [(row["file_id"], row["source_row_number"]) for row in disclosure["batches"][1]["preview"]["selected_rows"]]
    state = service.store.get(current["token"])
    revised = service.revise(current["token"], ImportReviseInput(expected_updated_time=latest["updated_time"],
        choices=[dict(file_id=key[0], source_row_number=key[1], **state.choices[key]) for key in remaining]))
    assert service.store.get(current["token"]).operation is None
    fresh, approved = approve(service, revised, remaining)
    assert fresh["operation_preview_digest"] != disclosure["operation_preview_digest"]
    final = commit_child(service, fresh, approved, 0)
    assert final["complete"] and final["new_fact_count"] == 2


def test_legacy_confirm_cannot_mutate_an_active_approved_chain(service):
    rows, current = pairs(service)
    disclosure, approved = approve(service, current, list(rows))
    payload = child_input(disclosure, approved, 0).model_dump(exclude={"operation_preview_digest", "batch_index"})
    before = manifest(service)
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        service.confirm(current["token"], ImportConfirmInput(**payload))
    assert manifest(service) == before


def test_serial_public_dtos_are_strict_and_preserve_1000_row_financial_limit(service, client):
    rows, current = independent(service, 1001)
    disclosure = operation(service, current, list(rows))
    import_preview_store.put(service.store.get(current["token"]))
    payload = ImportOperationApproveInput(**input_for(current, list(rows), operation_preview_digest=disclosure["operation_preview_digest"]))
    before = manifest(service)
    response = client.post(f"{BASE}/preview/{current['token']}/operation-approve", json=payload.model_dump(mode="json"))
    assert response.status_code == 200
    body = response.json()["body"]
    ImportOperationApprovalPO(**body)
    assert manifest(service) == before
    with pytest.raises(ValidationError):
        ImportOperationConfirmInput(**payload.model_dump(), batch_index=0, batch_preview_digest="0" * 64)
    assert client.post(f"{BASE}/preview/{current['token']}/operation-stop?extra=1",
        json=dict(operation_preview_digest=disclosure["operation_preview_digest"])).status_code == 422
    assert client.post(f"{BASE}/preview/{current['token']}/operation-stop",
        json=dict(operation_preview_digest=disclosure["operation_preview_digest"])).json()["body"] == dict(stopped=True)
    schema = client.get("/openapi.json").json()
    assert all(f"{BASE}/preview/{{token}}/{action}" in schema["paths"] for action in
        ("operation-approve", "operation-confirm", "operation-stop"))


def links(service):
    origin = prepare(service.mapper, [row(reference="")])
    id = accept(service.mapper, origin)["processed_rows"][0]["transaction_id"]
    first = prepare(service.mapper, [row(reference="")] + [row(i, reference=f"skip-{i}") for i in range(2, 1001)], sha="b" * 64)
    last = prepare(service.mapper, [row(reference="")], sha="c" * 64)
    keys = [min(first), min(last)]
    choices = {key: dict(decision="SKIP") for key in first | last}
    for key in keys:
        choices[key] = dict(decision="ACCEPT", resolution="LINK_EXISTING", target=dict(kind="FACT", transaction_id=id))
    rows = first | last
    return rows, install(service, rows, choices), id


def test_own_appended_source_proof_is_exactly_certified_across_children_without_new_cash(service):
    rows, current, id = links(service)
    disclosure, approved = approve(service, current, list(rows))
    before = manifest(service)
    first = commit_child(service, disclosure, approved, 0)
    assert first["manual_linked_count"] == 1 and first["skipped_count"] == 999
    assert service.store.get(current["token"]).operation["proofs"][str(id)]
    service.db.rollback()
    second = commit_child(service, disclosure, first, 1)
    assert second["manual_linked_count"] == 1 and second["complete"]
    after = manifest(service)
    for name in ("transaction_fact", "review_case", "ledger_entry", "review_transaction_ledger_allocation"):
        assert after[name] == before[name]


def test_unowned_evidence_append_does_not_get_rebased_with_our_own_proof(service):
    rows, current, id = links(service)
    disclosure, approved = approve(service, current, list(rows))
    first = commit_child(service, disclosure, approved, 0)
    extra = prepare(service.mapper, [row(reference="")], sha="d" * 64)
    choices = {key: dict(decision="ACCEPT", resolution="LINK_EXISTING", target=dict(kind="FACT", transaction_id=id)) for key in extra}
    service.mapper.begin_write()
    try:
        matched = service.mapper.match(extra, choices)
        service.mapper.write_batch(matched, choices, list(extra))
        service.db.commit()
    finally:
        service.mapper.end_write()
        service.db.rollback()
    before = manifest(service)
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        commit_child(service, disclosure, first, 1)
    assert manifest(service) == before


def test_approval_content_is_counted_in_existing_cache_capacity_not_hidden_in_side_storage(service):
    rows, current = pairs(service)
    disclosure = operation(service, current, list(rows))
    original = service.store.get(current["token"])
    service.store.single_bytes = original.byte_size() + 8
    before = manifest(service)
    with pytest.raises(TargetIntakeError) as error:
        service.approve_operation(current["token"], ImportOperationApproveInput(**input_for(current, list(rows),
            operation_preview_digest=disclosure["operation_preview_digest"])))
    assert error.value.code == "INPUT_LIMIT" and error.value.status_code == 413
    assert service.store.get(current["token"]) == original
    assert manifest(service) == before


def test_post_commit_cache_failure_is_unknown_and_never_leaves_the_old_child_retryable(service, monkeypatch):
    rows, current = pairs(service)
    disclosure, approved = approve(service, current, list(rows))
    def broken(_state, _lease):
        raise RuntimeError("Mock publish failure")
    monkeypatch.setattr(service.store, "publish", broken)
    with pytest.raises(TargetIntakeError) as error:
        commit_child(service, disclosure, approved, 0)
    assert error.value.code == "RESULT_UNKNOWN"
    assert service.db.scalar(select(func.count()).select_from(TransactionFact)) == 98
    assert service.store.get(current["token"]).operation["stopped"]
    before = manifest(service)
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        commit_child(service, disclosure, approved, 0)
    assert manifest(service) == before


def test_default_bulk_mapping_does_not_depend_on_returning_order_and_preserves_exact_units(service, monkeypatch):
    values = [dict(fact_key=f"Mock-unit-{index}", occurred_time=utc_now(),
        cash_direction=direction, amount=amount, currency_code=code, account_code=f"Mock-account-{index}",
        counterparty_account_ref=f"Mock-other-{index}", counterparty_name="", summary="")
        for index, (direction, amount, code) in enumerate([(1, 1, "JPY"), (2, 10000, "CNY"), (1, 10000, "CNY_4")])]
    service.db.add_all([TransactionFact(**value) for value in values])
    service.db.commit()
    originals = {row.id: row for row in service.db.scalars(select(TransactionFact))}
    scalars = service.db.scalars
    class Reversed:
        def __init__(self, result):
            self.rows = list(reversed(result.all()))
        def __iter__(self):
            return iter(self.rows)
        def all(self):
            return self.rows
    def reorder(statement, *args, **kwargs):
        result = scalars(statement, *args, **kwargs)
        return Reversed(result) if getattr(statement, "is_insert", False) else result
    monkeypatch.setattr(service.db, "scalars", reorder)
    service.mapper.begin_write()
    try:
        reviews, _, ledgers, _ = service.mapper.create_initial_defaults(list(reversed(originals)))
        service.db.commit()
    finally:
        service.mapper.end_write()
    assert len(reviews) == len(ledgers) == 3
    actual_ledgers = {row.id: row for row in service.db.scalars(select(LedgerEntry))}
    for allocation in service.db.scalars(select(ReviewAllocation)):
        fact, ledger = originals[allocation.transaction_id], actual_ledgers[allocation.ledger_id]
        assert ledger.amount == allocation.cash_amount == fact.amount
        assert ledger.currency_code == allocation.cash_currency_code == fact.currency_code
        assert ledger.entry_direction == fact.cash_direction
        assert ledger.account_code == fact.account_code and ledger.counterparty_account_ref == fact.counterparty_account_ref
    before = manifest(service)
    from backend.error import TargetEconomicError
    with pytest.raises(TargetEconomicError) as error:
        service.mapper.create_initial_defaults(list(originals))
    assert error.value.code == "DEFAULT_IDENTITY_REQUIRED"
    service.db.rollback()
    assert manifest(service) == before


def test_actual_http_compound_chain_returns_exact_tokens_for_the_next_child(service, client):
    tagged(service)
    rows, current = pairs(service)
    disclosure = operation(service, current, list(rows))
    import_preview_store.put(service.store.get(current["token"]))
    payload = ImportOperationApproveInput(**input_for(current, list(rows),
        operation_preview_digest=disclosure["operation_preview_digest"]))
    service.db.rollback()
    response = client.post(f"{BASE}/preview/{current['token']}/operation-approve", json=payload.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    latest = response.json()["body"]
    results = []
    for index in range(2):
        response = client.post(f"{BASE}/preview/{current['token']}/operation-confirm",
            json=child_input(disclosure, latest, index).model_dump(mode="json"))
        assert response.status_code == 200, response.text
        latest = response.json()["body"]
        ImportOperationConfirmPO(**latest)
        results.append(latest)
    assert [len(result["processed_rows"]) for result in results] == [98, 2]
    assert latest["complete"] and latest["next_batch_index"] == 2
    assert service.db.scalar(select(func.count()).select_from(TransactionFact)) == 100
    assert import_preview_store.get(current["token"]).operation is None


@pytest.mark.parametrize("kind", ["wrong_digest", "blocked_group"])
def test_no_approval_of_an_undisclosed_or_blocked_operation(service, kind):
    if kind == "blocked_group":
        rows = prepare(service.mapper, [row(index) for index in range(1, 1002)])
        current = install(service, rows, {key: dict(decision="ACCEPT", resolution="NEW", acknowledge_new_risk=True) for key in rows})
    else:
        rows, current = pairs(service)
    disclosure = operation(service, current, list(rows))
    before = manifest(service)
    payload = ImportOperationApproveInput(**input_for(current, list(rows),
        operation_preview_digest="0" * 64 if kind == "wrong_digest" else disclosure["operation_preview_digest"]))
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        service.approve_operation(current["token"], payload)
    assert manifest(service) == before and service.store.get(current["token"]).operation is None
