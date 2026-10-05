"""Informed whole-operation previews against isolated fictional SQLite only."""
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select, update

from backend.core.import_preview_store import import_preview_store
from backend.error import TargetEconomicError, TargetIntakeError
from backend.entity import LedgerEntry, ReviewCase, TargetTag, TargetTagView, AutoTagRule
from backend.schema.import_command import ImportOperationPreviewInput, ImportConfirmPreviewInput
from backend.schema.import_batch_read import ImportOperationPreviewPO
from backend.service.import_operation_service import row_ranges
from test_pirc35_import_api import client, BASE
from test_pirc35_import_service import service
from test_pirc35_import_batch import prepare, row, accept
from test_pirc35_import_confirm_preview import install, input_for, batch, manual_fact
from test_pirc35_import_duplicate import manifest, many_local_pairs, other_row, decision


def operation(service, current, keys):
    return service.operation_preview(current["token"], ImportOperationPreviewInput(**input_for(current, keys)))


def independent(service, n):
    rows = prepare(service.mapper, [row(i, reference=f"business-{i}") for i in range(1, n + 1)])
    current = install(service, rows, {key: dict(decision="ACCEPT", resolution="NEW", acknowledge_new_risk=True) for key in rows})
    return rows, current


def flatten(result):
    return [(row["file_id"], row["source_row_number"]) for item in result["batches"] for row in item["preview"]["selected_rows"]]


def test_real_2500_scope_discloses_1000_1000_500_complete_effects_without_writes(service):
    rows, current = independent(service, 2500)
    before = manifest(service)
    retained = service.store.get(current["token"])
    result = operation(service, current, list(reversed(rows)))
    ImportOperationPreviewPO(**result)
    assert result["selected_count"] == 2500 and result["can_confirm"] and result["blocked"] == []
    assert [len(item["preview"]["selected_rows"]) for item in result["batches"]] == [1000, 1000, 500]
    assert flatten(result) == sorted(rows)
    assert [item["preview"]["budget"]["review_groups"] for item in result["batches"]] == [1000, 1000, 500]
    assert sum(item["preview"]["effects"]["by_currency"][0]["cash_out_amount"] for item in result["batches"]) == 100_000_000
    assert all(pair["duplicate_hint"]["candidate_count"] == 2499 for item in result["batches"] for pair in item["preview"]["pairs"])
    assert not result["cross_batch_atomic"] and not result["execution_policy"]["automatic_post_replay"]
    assert result["execution_policy"]["stop_on"] == ["CANCEL", "FAILURE", "STALE_PREVIEW", "RESULT_UNKNOWN"]
    assert result["execution_policy"]["retain_committed"] is True
    assert "0000000000123456" not in str(result) and "raw_payload" not in str(result)
    assert manifest(service) == before
    assert service.store.get(current["token"]) == retained
    assert len(result["operation_preview_digest"]) == 64 and result["operation_preview_digest"] != current["preview_digest"]
    # An operation preview is not authority to send 2500 rows to one writer.
    with pytest.raises(ValidationError):
        ImportConfirmPreviewInput(**input_for(current, list(rows)))


def test_fifty_actual_new_dup_pairs_partition_full_101_review_impact(service):
    rows, choices = many_local_pairs(service, 50)
    current = install(service, rows, choices)
    before = manifest(service)
    result = operation(service, current, list(rows))
    ImportOperationPreviewPO(**result)
    assert result["can_confirm"] and [len(item["preview"]["selected_rows"]) for item in result["batches"]] == [98, 2]
    assert [item["preview"]["budget"]["review_groups"] for item in result["batches"]] == [99, 3]
    assert [item["preview"]["counts"]["new_duplicate_fact"] for item in result["batches"]] == [49, 1]
    assert [len(item["duplicate_revoke_scope"]) for item in result["batches"]] == [49, 1]
    assert sorted(flatten(result)) == sorted(rows)
    for item in result["batches"]:
        keys = {(row["file_id"], row["source_row_number"]) for row in item["preview"]["selected_rows"]}
        for key in keys:
            target = choices[key].get("target")
            if target:
                assert (target["file_id"], target["source_row_number"]) in keys
        assert item["preview"] == batch(service, current, list(keys))
    assert manifest(service) == before


def test_pure_prefix_cannot_hide_compound_expanded_budget_or_split_row_pair(service):
    ordinary = [row(i, reference=f"A-{i}", amount_minor=-i) for i in range(1, 105)]
    anchors = prepare(service.mapper, ordinary)
    anchor = next(key for key in anchors if key[1] == 103)
    sources = prepare(service.mapper, [other_row(reference="B", amount_minor=-103)], sha="b" * 64)
    source = next(iter(sources))
    rows = anchors | sources
    choices = {key: dict(decision="ACCEPT", resolution="NEW", acknowledge_new_risk=True) for key in anchors}
    choices[source] = decision(dict(kind="ROW", file_id=anchor[0], source_row_number=anchor[1]))
    current = install(service, rows, choices)
    result = operation(service, current, list(rows))
    assert result["can_confirm"] and [len(item["preview"]["selected_rows"]) for item in result["batches"]] == [102, 3]
    assert [item["preview"]["budget"]["review_groups"] for item in result["batches"]] == [102, 4]
    assert (anchor in flatten(result)) and (source in flatten(result))
    assert result["batches"][1]["duplicate_revoke_scope"] == [dict(file_id=source[0], source_row_number=source[1])]


def test_oversized_source_group_is_complete_blocker_and_independent_rows_stay_visible(service):
    rows = prepare(service.mapper, [row(i) for i in range(1, 1002)] + [row(1002, reference="another-real")])
    current = install(service, rows, {key: dict(decision="ACCEPT", resolution="NEW", acknowledge_new_risk=True) for key in rows})
    before = manifest(service)
    result = operation(service, current, list(rows))
    ImportOperationPreviewPO(**result)
    assert not result["can_confirm"] and len(result["blocked"]) == 1
    blocked = result["blocked"][0]
    assert len(blocked["selected_rows"]) == 1001 and blocked["budget"]["facts"] == 1
    assert blocked["issue"] == dict(code="INPUT_LIMIT", dimension="selected_rows", count=1001, limit=1000,
        action="EXCLUDE_OR_REVIEW_BLOCKED_GROUP")
    assert len(flatten(result)) == 1 and len(flatten(result)) + len(blocked["selected_rows"]) == 1002
    assert manifest(service) == before


def test_shared_existing_keeper_whole_groups_and_tags_are_deduplicated(service):
    origin = prepare(service.mapper, [row(reference="A")])
    id = accept(service.mapper, origin)["processed_rows"][0]["transaction_id"]
    b = prepare(service.mapper, [other_row(reference="B")], sha="b" * 64)
    c = prepare(service.mapper, [other_row(reference="C", account="0000000000888888")], sha="c" * 64)
    rows = b | c
    current = install(service, rows, {key: decision(dict(kind="FACT", transaction_id=id)) for key in rows})
    result = operation(service, current, list(rows))
    child = result["batches"][0]["preview"]
    assert result["can_confirm"] and child["budget"] == dict(selected_rows=2, review_groups=4, facts=3,
        outputs=5, position_links=5, tag_changes=0)
    assert child == batch(service, current, list(rows))


def test_operation_sql_reads_are_bulk_not_one_query_per_component(service):
    rows, current = independent(service, 100)
    counts = []
    for keys in (list(rows)[:1], list(rows)):
        service.db.rollback()
        statements = []
        def capture(_connection, _cursor, sql, _parameters, _context, _many):
            statements.append(sql)
        event.listen(service.db.bind, "before_cursor_execute", capture)
        try:
            result = operation(service, current, keys)
        finally:
            event.remove(service.db.bind, "before_cursor_execute", capture)
        assert result["can_confirm"]
        assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "BEGIN IMMEDIATE")) for sql in statements)
        counts.append(len(statements))
    assert counts[0] == counts[1]


def test_operation_http_contract_accepts_1001_and_has_concrete_openapi(service, client):
    rows, current = independent(service, 1001)
    import_preview_store.put(service.store.get(current["token"]))
    before = manifest(service)
    payload = input_for(current, list(rows))
    payload["expected_updated_time"] = payload["expected_updated_time"].isoformat()
    response = client.post(BASE + f"/preview/{current['token']}/operation-preview", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()["body"]
    ImportOperationPreviewPO(**result)
    assert [item["preview"]["budget"]["selected_rows"] for item in result["batches"]] == [1000, 1]
    assert manifest(service) == before
    schema = client.get("/openapi.json").json()
    route = schema["paths"][BASE + "/preview/{token}/operation-preview"]["post"]
    assert route["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("ImportOperationPreviewInput")
    assert "ImportOperationPreviewResponse" in str(route["responses"]["200"])
    assert schema["components"]["schemas"]["ImportOperationPreviewInput"]["properties"]["selected_rows"]["maxItems"] == 20000
    assert schema["components"]["schemas"]["ImportConfirmInput"]["properties"]["selected_rows"]["maxItems"] == 1000


@pytest.mark.parametrize("keys", [[], [dict(file_id=1, source_row_number=1)] * 2,
    [dict(file_id=True, source_row_number=1)], [dict(file_id=1, source_row_number=0)],
    [dict(file_id=1, source_row_number=n) for n in range(1, 20002)]])
def test_operation_selection_remains_strict_and_bounded(keys):
    with pytest.raises(ValidationError):
        ImportOperationPreviewInput(expected_updated_time="2024-01-01T00:00:00Z", preview_digest="a" * 64, selected_rows=keys)


def test_range_disclosure_preserves_gaps_and_different_files():
    assert row_ranges([(2, 1), (1, 4), (1, 2), (1, 1), (2, 3)]) == [
        dict(file_id=1, row_start=1, row_end=2, row_count=2), dict(file_id=1, row_start=4, row_end=4, row_count=1),
        dict(file_id=2, row_start=1, row_end=1, row_count=1), dict(file_id=2, row_start=3, row_end=3, row_count=1)]


def test_unresolved_risk_is_complete_and_never_turned_into_an_implicit_skip(service):
    rows = prepare(service.mapper, [row(n, reference="") for n in (1, 2)])
    current = install(service, rows, {key: dict(decision="ACCEPT") for key in rows})
    result = operation(service, current, list(rows))
    assert not result["can_confirm"] and len(flatten(result)) == 2 and result["blocked"] == []
    child = result["batches"][0]["preview"]
    assert child["counts"]["unresolved"] == 2 and child["effects"]["by_currency"][0]["cash_out_amount"] == 80000


def test_whole_response_limit_cannot_return_a_partial_plan(service, monkeypatch):
    import backend.service.import_operation_service as module
    rows, current = independent(service, 2)
    before = manifest(service)
    monkeypatch.setattr(module, "MAX_OPERATION_BYTES", 1)
    with pytest.raises(TargetIntakeError) as error:
        operation(service, current, list(rows))
    assert error.value.code == "DETAIL_LIMIT" and error.value.status_code == 413
    assert manifest(service) == before
    assert service.store.get(current["token"]).updated_time == current["updated_time"]


def test_late_choice_change_invalidates_complete_operation_read(service, monkeypatch):
    from backend.service.import_operation_service import ImportOperationService
    rows, current = independent(service, 2)
    original = ImportOperationService.plan
    def changed(self, state, order, candidates, risks, digest, **kwargs):
        result = original(self, state, order, candidates, risks, digest, **kwargs)
        latest = service.store.get(current["token"])
        latest.choices[next(iter(rows))]["decision"] = "SKIP"
        service.store.replace(latest, latest.updated_time)
        return result
    monkeypatch.setattr(ImportOperationService, "plan", changed)
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        operation(service, current, list(rows))
    assert not service.db.in_transaction()


def test_operation_cannot_omit_single_batch_relation_integrity_guard(service):
    origin = prepare(service.mapper, [row(reference="previous-real")], sha="d" * 64)
    accept(service.mapper, origin)
    rows, current = independent(service, 2)
    service.db.execute(update(LedgerEntry).values(account_ref_id=99999))
    service.db.commit()
    before = manifest(service)
    for preview in (operation, batch):
        with pytest.raises(TargetEconomicError) as error:
            preview(service, current, list(rows))
        assert error.value.code == "ACCOUNT_RELATION_BROKEN"
        assert not service.db.in_transaction()
        assert manifest(service) == before


def test_operation_validates_relations_once_not_once_per_batch(service, monkeypatch):
    from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
    rows, current = independent(service, 1001)
    original, checks = TrustedRelationMapper.validate, []
    def check(self):
        checks.append(True)
        return original(self)
    monkeypatch.setattr(TrustedRelationMapper, "validate", check)
    result = operation(service, current, list(rows))
    assert result["can_confirm"] and len(result["batches"]) == 2
    assert checks == [True]


def test_whole_manual_group_projection_matches_standalone_child(service):
    current, keys, _ids = manual_fact(service, group=True)
    result = operation(service, current, keys)
    child = result["batches"][0]["preview"]
    assert result["can_confirm"] and child == batch(service, current, keys)
    assert child["budget"]["facts"] == 2
    assert child["budget"]["review_groups"] == 2
    assert child["counts"]["evidence_only"] == 1
    assert child["effects"]["new_original_defaults"] == []


def test_operation_full_tags_and_disabled_rule_budget_matches_each_child(service):
    service.db.add(TargetTagView(id=1, name="Mock purpose", system_name="mock-purpose", status="ACTIVE"))
    service.db.add(TargetTag(id=1, view_id=1, name="Mock unclassified", system_name="unclassified", status="ACTIVE"))
    service.db.add(AutoTagRule(id=1, name="Mock disabled rule", view_id=1, method=1,
        method_config_json='{"schema_version":1,"model_id":9,"prompt":"Mock"}',
        enabled=0, cron="", amount_mode=1, scan_after_ledger_id=100, scan_epoch=1, rule_revision=1))
    service.db.commit()
    rows, choices = many_local_pairs(service, 50)
    current = install(service, rows, choices)
    before = manifest(service)
    result = operation(service, current, list(rows))
    assert [item["preview"]["budget"]["tag_changes"] for item in result["batches"]] == [148, 4]
    for item in result["batches"]:
        keys = [(row["file_id"], row["source_row_number"]) for row in item["preview"]["selected_rows"]]
        assert item["preview"] == batch(service, current, keys)
        assert item["preview"]["effects"]["tag_effect"]["affected_rule_ids"] == [1]
    assert manifest(service) == before
    service.db.execute(update(TargetTag).where(TargetTag.id == 1).values(name="Mock changed default"))
    service.db.commit()
    changed = operation(service, current, list(rows))
    assert changed["operation_preview_digest"] != result["operation_preview_digest"]
    assert all(new["preview"]["batch_preview_digest"] != old["preview"]["batch_preview_digest"]
        for old, new in zip(result["batches"], changed["batches"]))


def test_operation_group_reads_share_one_wal_snapshot(service):
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
        result = operation(service, current, keys)
    finally:
        event.remove(service.db.bind, "after_cursor_execute", change_review)
    child = result["batches"][0]["preview"]
    assert changed and child["expected_reviews"][0]["status"] == "CONFIRMED"
    assert child["effects"]["before_after_review_states"][0]["before"]["status"] == "CONFIRMED"
    assert service.db.scalar(select(ReviewCase.status)) == 1
    assert operation(service, current, keys)["operation_preview_digest"] != result["operation_preview_digest"]


@pytest.mark.parametrize("guard", ["busy", "time", "digest", "missing"])
def test_operation_rejects_invalid_preview_context_without_writes(service, guard):
    from contextlib import nullcontext
    rows, current = independent(service, 1)
    payload = input_for(current, list(rows))
    if guard == "time":
        payload["expected_updated_time"] -= timedelta(microseconds=1)
    elif guard == "digest":
        payload["preview_digest"] = "0" * 64
    elif guard == "missing":
        payload["selected_rows"][0]["source_row_number"] += 1
    before = manifest(service)
    # get() returns an isolated copy, and derives CONFIRMING from a real claim;
    # assigning status on that copy is not evidence of an in-flight operation.
    ownership = service.store.claim(current["token"], current["updated_time"]) if guard == "busy" else nullcontext()
    with ownership:
        with pytest.raises(TargetIntakeError) as error:
            service.operation_preview(current["token"], ImportOperationPreviewInput(**payload))
    assert error.value.code == {"busy": "PREVIEW_BUSY", "time": "STALE_PREVIEW",
        "digest": "STALE_PREVIEW", "missing": "PREVIEW_ROW_NOT_FOUND"}[guard]
    assert manifest(service) == before


def test_missing_choices_stay_visible_and_block_whole_operation(service):
    rows = prepare(service.mapper, [row(n, reference=f"missing-{n}") for n in (1, 2)])
    current = install(service, rows, {})
    result = operation(service, current, list(rows))
    assert not result["can_confirm"] and result["blocked"] == []
    child = result["batches"][0]["preview"]
    assert len(flatten(result)) == child["counts"]["unresolved"] == 2
    assert {issue["code"] for issue in child["issues"]} == {"ROW_CHOICE_REQUIRED"}
    assert child["effects"]["new_original_defaults"] == []
