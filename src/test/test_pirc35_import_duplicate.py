"""New cross-source B/default/DUP stays inside one fictional import write."""
import pytest
from sqlalchemy import event, select, update, text

from backend.core import target_database
from backend.entity import (TransactionFact, ReviewCase, LedgerEntry, ReviewAllocation, TransactionImportRow,
                            TargetTag, TargetTagView, LedgerEntryTag, AutoTagRule)
from backend.error import TargetEconomicError, TargetIntakeError
from backend.schema.import_command import ImportConfirmInput
from backend.schema.import_batch_read import ImportBatchPreviewPO, ImportConfirmPO
from test_pirc35_import_service import service  # noqa: F401
from test_pirc35_import_batch import prepare, row, accept, count
from test_pirc35_import_confirm_preview import install, batch, input_for
from test_pirc35_review_command import execute, duplicate


def other_row(n=1, account="0000000000999999", **changes):
    return row(n, account=dict(number=account), source_account=dict(source_namespace="ccb:statement-v1",
        source_identity=account, identity_strength="RELIABLE"), **changes)


def decision(target):
    return dict(decision="ACCEPT", resolution="DUPLICATE", target=target)


def existing_pair(service, **changes):
    origin = prepare(service.mapper, [row(reference="origin-A")])
    id = accept(service.mapper, origin)["processed_rows"][0]["transaction_id"]
    rows = prepare(service.mapper, [other_row(reference="new-B", **changes)], sha="b" * 64)
    key = next(iter(rows))
    return id, rows, {key: decision(dict(kind="FACT", transaction_id=id))}


def manifest(service):
    return {name: [dict(row) for row in service.db.execute(select(table).order_by(table.c.id)).mappings()]
            for name, table in target_database.TargetBase.metadata.tables.items()}


def confirm_pair(service, current, keys, disclosure, **kwargs):
    return service.confirm(current["token"], ImportConfirmInput(**input_for(current, keys,
        batch_preview_digest=disclosure["batch_preview_digest"])), **kwargs)


def test_existing_keeper_preview_and_atomic_import_disclose_original_and_effective_ids(service):
    id, rows, choices = existing_pair(service)
    current = install(service, rows, choices)
    before = manifest(service)
    disclosure = batch(service, current, list(rows))
    ImportBatchPreviewPO(**disclosure)
    assert manifest(service) == before
    assert disclosure["counts"]["new_real_fact"] == 0 and disclosure["counts"]["new_duplicate_fact"] == 1
    assert disclosure["budget"]["review_groups"] == 3
    assert disclosure["budget"]["outputs"] == disclosure["budget"]["position_links"] == 3
    assert disclosure["effects"]["new_original_defaults"][0]["after_status"] == "REVOKED"
    assert disclosure["effects"]["by_currency"] == [dict(currency_code="CNY", cash_in_amount=0,
        cash_out_amount=0, excluded_in_amount=0, excluded_out_amount=40000)]
    assert len(disclosure["effects"]["new_duplicate_reviews"]) == 1
    result = confirm_pair(service, current, list(rows), disclosure)
    ImportConfirmPO(**result)
    assert result["new_fact_count"] == result["duplicate_fact_count"] == 1
    output = result["processed_rows"][0]
    assert output["resolution_effect"] == "DUPLICATE_ZERO" and output["duplicate_kept_transaction_id"] == id
    assert output["created_review_id"] not in output["effective_review_ids"]
    assert output["created_ledger_id"] not in output["effective_ledger_ids"]
    original = service.db.get(ReviewCase, output["created_review_id"])
    assert original.behavior_type == 0 and original.status == 1
    effective = service.db.get(LedgerEntry, output["effective_ledger_ids"][0])
    assert effective.entry_type == 3 and effective.amount == 40000
    assert service.db.get(ReviewCase, 1).status == 0
    for table in ("transaction_fact", "review_case", "ledger_entry", "review_transaction_ledger_allocation"):
        now = manifest(service)[table]
        assert now[:len(before[table])] == before[table]
    assert result["remaining_count"] == 0 and result["files"][0]["status"] == 1


def local_pair(service, *, link=False):
    anchors = prepare(service.mapper, [row(reference="new-A")])
    excluded = prepare(service.mapper, [other_row(reference="new-B")], sha="b" * 64)
    anchor, source = next(iter(anchors)), next(iter(excluded))
    choices = {anchor: dict(decision="ACCEPT", resolution="NEW", acknowledge_new_risk=True), source: decision(dict(kind="ROW",
        file_id=anchor[0], source_row_number=anchor[1]))}
    rows = anchors | excluded
    if link:
        supplement = prepare(service.mapper, [row(reference="")], sha="c" * 64)
        key = next(iter(supplement))
        choices[key] = dict(decision="ACCEPT", resolution="LINK_EXISTING",
            target=dict(kind="ROW", file_id=anchor[0], source_row_number=anchor[1]))
        rows.update(supplement)
    return rows, choices, anchor, source


def test_local_real_anchor_same_source_link_and_cross_source_b_use_actual_ids_once(service):
    rows, choices, anchor, source = local_pair(service, link=True)
    current = install(service, rows, choices)
    disclosure = batch(service, current, list(reversed(rows)))
    assert disclosure["counts"] == dict(new_real_fact=1, new_duplicate_fact=1, evidence_only=1,
        skipped=0, invalid=0, unresolved=0)
    assert disclosure["budget"]["review_groups"] == 3 and disclosure["budget"]["facts"] == 2
    result = confirm_pair(service, current, list(reversed(rows)), disclosure)
    ids = {(value["file_id"], value["source_row_number"]): value for value in result["processed_rows"]}
    assert ids[source]["duplicate_kept_transaction_id"] == ids[anchor]["transaction_id"]
    evidence = next(value for key, value in ids.items() if key not in {anchor, source})
    assert evidence["transaction_id"] == ids[anchor]["transaction_id"] and evidence["created_review_id"] == 0
    assert result["new_fact_count"] == 2 and result["manual_linked_count"] == result["duplicate_fact_count"] == 1
    assert count(service.mapper, TransactionFact) == 2 and count(service.mapper, ReviewCase) == 3


@pytest.mark.parametrize("stage", ["facts", "defaults", "tags", "source_rows", "duplicate_identities",
    "duplicate_outputs", "duplicate_relations", "duplicate_tags", "file_counts", "before_commit"])
def test_every_import_and_duplicate_stage_rolls_back_complete_twenty_table_state(service, stage):
    _id, rows, choices = existing_pair(service)
    current = install(service, rows, choices)
    disclosure = batch(service, current, list(rows))
    before = manifest(service)
    def fault(actual):
        if actual == stage:
            raise RuntimeError("fictional compound fault")
    with pytest.raises(RuntimeError, match="compound fault"):
        confirm_pair(service, current, list(rows), disclosure, fault=fault)
    assert manifest(service) == before


@pytest.mark.parametrize("change", [dict(amount_minor=-39999), dict(amount_minor=40000),
    dict(currency="CNY_4"), dict(occurred_at="2024-01-01T00:00:00.000001Z"),
    dict(account="0000000000123456")])
def test_exact_core_or_same_original_source_rejects_without_new_cash(service, change):
    _id, rows, choices = existing_pair(service, **change)
    before = manifest(service)
    with pytest.raises(TargetIntakeError) as error:
        install(service, rows, choices)
    assert error.value.code in {"FACT_CONFLICT", "INVALID_DUPLICATE"}
    assert manifest(service) == before


def test_direct_mapper_cannot_commit_only_new_b_default(service):
    _id, rows, choices = existing_pair(service)
    before = manifest(service)
    service.mapper.begin_write()
    try:
        candidates = service.mapper.match(rows, choices)
        with pytest.raises(TargetIntakeError, match="IMPORT_REVIEW_REQUIRED"):
            service.mapper.write_batch(candidates, choices, list(rows))
    finally:
        service.mapper.end_write()
        service.db.rollback()
    assert manifest(service) == before


def test_missing_batch_digest_does_not_begin_or_create_b(service):
    _id, rows, choices = existing_pair(service)
    current = install(service, rows, choices)
    before = manifest(service)
    with pytest.raises(TargetIntakeError, match="IMPORT_REVIEW_REQUIRED"):
        service.confirm(current["token"], ImportConfirmInput(**input_for(current, list(rows))))
    assert manifest(service) == before


def test_committed_response_loss_retains_duplicate_and_does_not_replay(service):
    _id, rows, choices = existing_pair(service)
    current = install(service, rows, choices)
    disclosure = batch(service, current, list(rows))
    def lost(stage):
        if stage == "after_commit":
            raise RuntimeError("fictional lost response")
    with pytest.raises(TargetIntakeError, match="RESULT_UNKNOWN"):
        confirm_pair(service, current, list(rows), disclosure, fault=lost)
    before = manifest(service)
    assert count(service.mapper, TransactionFact) == 2 and count(service.mapper, ReviewCase) == 3
    with pytest.raises(TargetIntakeError, match="ROWS_ALREADY_PROCESSED"):
        confirm_pair(service, current, list(rows), disclosure)
    assert manifest(service) == before


def test_revoke_duplicate_restores_its_unique_original_without_rewriting_a(service):
    _id, rows, choices = existing_pair(service)
    current = install(service, rows, choices)
    result = confirm_pair(service, current, list(rows), batch(service, current, list(rows)))
    value = result["processed_rows"][0]
    execute(service.db, deactivate_review_ids=value["effective_review_ids"])
    assert service.db.get(ReviewCase, value["created_review_id"]).status == 0
    assert service.db.get(ReviewCase, 1).status == 0
    assert count(service.mapper, ReviewCase) == 3 and count(service.mapper, TransactionImportRow) == 2


def many_local_pairs(service, n, *, extra=False):
    anchors = prepare(service.mapper, [row(i, reference=f"A-{i}",
        occurred_at=f"2024-01-01T00:00:{i:02}Z") for i in range(1, n + 1)] +
        ([row(n + 1, reference="ordinary-extra")] if extra else []))
    excluded = prepare(service.mapper, [other_row(i, reference=f"B-{i}",
        occurred_at=f"2024-01-01T00:00:{i:02}Z") for i in range(1, n + 1)], sha="b" * 64)
    choices = {key: dict(decision="ACCEPT", resolution="NEW", acknowledge_new_risk=True) for key in anchors}
    for key in excluded:
        anchor = next(key_a for key_a in anchors if key_a[1] == key[1])
        choices[key] = decision(dict(kind="ROW", file_id=anchor[0], source_row_number=anchor[1]))
    return anchors | excluded, choices


@pytest.mark.parametrize("extra,groups", [(False, 99), (True, 100)])
def test_49_new_pairs_count_every_original_and_one_duplicate_group(service, extra, groups):
    rows, choices = many_local_pairs(service, 49, extra=extra)
    current = install(service, rows, choices)
    before = manifest(service)
    disclosure = batch(service, current, list(rows))
    assert disclosure["budget"]["review_groups"] == groups
    assert disclosure["counts"]["new_duplicate_fact"] == 49
    assert disclosure["budget"]["outputs"] == disclosure["budget"]["position_links"] == 147 + extra
    assert manifest(service) == before


def test_50_new_pairs_reject_101_expanded_groups_before_creating_any_fact(service):
    rows, choices = many_local_pairs(service, 50)
    current = install(service, rows, choices)
    before = manifest(service)
    with pytest.raises(TargetIntakeError) as error:
        batch(service, current, list(rows))
    assert error.value.status_code == 413 and error.value.code == "REVIEW_CHANGE_LIMIT"
    assert manifest(service) == before and count(service.mapper, TransactionFact) == 0


@pytest.mark.parametrize("case", ["self", "mutual", "missing", "skip"])
def test_row_keeper_must_be_selected_real_and_not_a_duplicate_chain(service, case):
    rows, choices, anchor, source = local_pair(service)
    if case == "self":
        choices[source]["target"] = dict(kind="ROW", file_id=source[0], source_row_number=source[1])
    elif case == "mutual":
        choices[anchor] = decision(dict(kind="ROW", file_id=source[0], source_row_number=source[1]))
    elif case == "missing":
        rows.pop(anchor)
        choices.pop(anchor)
    else:
        choices[anchor] = dict(decision="SKIP")
    before = manifest(service)
    with pytest.raises(TargetIntakeError, match="INVALID_EVIDENCE_TARGET"):
        install(service, rows, choices)
    assert manifest(service) == before


def test_same_file_two_independent_bs_cannot_broadcast_one_keeper(service):
    id, rows, _choices = existing_pair(service)
    second = other_row(2, reference="another-real-B")
    # A fresh fictional file must declare both immutable source rows.
    rows = prepare(service.mapper, [next(iter(rows.values())), second], sha="c" * 64)
    choices = {key: decision(dict(kind="FACT", transaction_id=id)) for key in rows}
    before = manifest(service)
    with pytest.raises(TargetIntakeError, match="IDENTITY_AMBIGUOUS"):
        install(service, rows, choices)
    assert manifest(service) == before


def test_reliable_same_b_key_two_evidence_rows_have_one_default_and_one_exclusion(service):
    id, _rows, _choices = existing_pair(service)
    rows = prepare(service.mapper, [other_row(n, reference="same-B") for n in (1, 2)], sha="c" * 64)
    choices = {key: decision(dict(kind="FACT", transaction_id=id)) for key in rows}
    current = install(service, rows, choices)
    disclosure = batch(service, current, list(rows))
    assert disclosure["counts"]["new_duplicate_fact"] == disclosure["counts"]["evidence_only"] == 1
    result = confirm_pair(service, current, list(reversed(rows)), disclosure)
    assert result["new_fact_count"] == result["duplicate_fact_count"] == 1
    assert len({value["transaction_id"] for value in result["processed_rows"]}) == 1
    assert sorted(value["resolution_effect"] for value in result["processed_rows"]) == ["DUPLICATE_ZERO", "EVIDENCE_ONLY"]


def test_a_currently_excluded_is_not_a_final_real_keeper(service):
    id, rows, choices = existing_pair(service)
    other = prepare(service.mapper, [other_row(account="0000000000777777", reference="keeper-C")], sha="c" * 64)
    other_id = accept(service.mapper, other)["processed_rows"][0]["transaction_id"]
    original = service.mapper.allocations_for_facts([id], defaults=True)[0]
    ref = service.db.get(LedgerEntry, original["ledger_id"]).account_ref_id
    execute(service.db, new_reviews=[duplicate(id, other_id, ref)])
    current = install(service, rows, choices)
    before = manifest(service)
    with pytest.raises(TargetEconomicError, match="kept Fact") as error:
        batch(service, current, list(rows))
    assert error.value.code == "INVALID_DUPLICATE" and manifest(service) == before


def test_existing_keeper_metadata_changed_after_disclosure_invalidates_confirm(service):
    _id, rows, choices = existing_pair(service)
    current = install(service, rows, choices)
    disclosure = batch(service, current, list(rows))
    service.db.get(ReviewCase, 1).title = "Mock changed explanation title"
    service.db.commit()
    before = manifest(service)
    with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
        confirm_pair(service, current, list(rows), disclosure)
    assert manifest(service) == before


def test_24_existing_keepers_and_new_bs_commit_once_and_preserve_all_a_outputs(service):
    service.db.execute(text("PRAGMA journal_mode=WAL"))
    service.db.commit()
    origins = prepare(service.mapper, [row(i, reference=f"A-{i}",
        occurred_at=f"2024-01-01T00:00:{i:02}Z") for i in range(1, 25)])
    accepted = accept(service.mapper, origins)
    ids = {value["source_row_number"]: value["transaction_id"] for value in accepted["processed_rows"]}
    rows = prepare(service.mapper, [other_row(i, reference=f"B-{i}",
        occurred_at=f"2024-01-01T00:00:{i:02}Z") for i in range(1, 25)], sha="b" * 64)
    current = install(service, rows, {key: decision(dict(kind="FACT", transaction_id=ids[key[1]])) for key in rows})
    disclosure = batch(service, current, list(rows))
    assert disclosure["budget"]["review_groups"] == 49 and disclosure["budget"]["outputs"] == 72
    commits, begins = [], []
    def committed(connection):
        commits.append(connection)
    def sql(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("BEGIN IMMEDIATE"):
            begins.append(statement)
    def fault(stage):
        if stage in {"defaults", "duplicate_tags", "before_commit"}:
            with target_database.SessionLocal() as reader:
                assert len(reader.execute(select(TransactionFact.id)).all()) == 24
                assert len(reader.execute(select(ReviewCase.id)).all()) == 24
    engine = service.db.get_bind()
    event.listen(engine, "commit", committed)
    event.listen(engine, "before_cursor_execute", sql)
    try:
        result = confirm_pair(service, current, list(rows), disclosure, fault=fault)
    finally:
        event.remove(engine, "commit", committed)
        event.remove(engine, "before_cursor_execute", sql)
    assert len(commits) == len(begins) == 1
    assert result["new_fact_count"] == result["duplicate_fact_count"] == 24
    assert len({value["effective_review_ids"][0] for value in result["processed_rows"]}) == 1
    assert count(service.mapper, TransactionFact) == 48 and count(service.mapper, ReviewCase) == 49
    assert all(service.db.get(ReviewCase, i).status == 0 for i in range(1, 25))


@pytest.mark.parametrize("stage", ["defaults", "source_rows", "duplicate_tags", "source_pointer"])
def test_actual_default_mapping_old_keeper_and_final_output_are_revalidated(service, stage):
    _id, rows, choices = existing_pair(service)
    current = install(service, rows, choices)
    disclosure = batch(service, current, list(rows))
    before = manifest(service)
    def fault(actual):
        if actual != ("duplicate_tags" if stage == "source_pointer" else stage):
            return
        if stage == "defaults":
            service.db.execute(update(LedgerEntry).where(LedgerEntry.id == 2).values(account_ref_id=1))
        elif stage == "source_rows":
            service.db.execute(update(ReviewCase).where(ReviewCase.id == 1).values(title="Mock unexpected mutation"))
        elif stage == "duplicate_tags":
            service.db.execute(update(LedgerEntry).where(LedgerEntry.entry_type == 3).values(entry_type=0))
        else:
            service.db.execute(update(TransactionImportRow).where(TransactionImportRow.transaction_import_file_id == next(iter(rows))[0])
                .values(transaction_fact_id=1))
    with pytest.raises((TargetEconomicError, TargetIntakeError)) as error:
        confirm_pair(service, current, list(rows), disclosure, fault=fault)
    assert error.value.code in {"IDENTITY_CHANGED", "STALE_PREVIEW", "INVALID_DUPLICATE"}
    assert manifest(service) == before


def test_a_custom_tags_are_unchanged_and_b_default_and_duplicate_use_unclassified(service):
    service.db.add(TargetTagView(id=1, name="Mock purpose", system_name="mock-purpose", status="ACTIVE"))
    service.db.add_all([TargetTag(id=1, view_id=1, name="Mock unclassified", system_name="unclassified", status="ACTIVE"),
        TargetTag(id=2, view_id=1, name="Mock custom", system_name="mock-custom", status="ACTIVE")])
    service.db.commit()
    id, rows, choices = existing_pair(service)
    allocation = service.mapper.allocations_for_facts([id], defaults=True)[0]
    service.db.execute(update(LedgerEntryTag).where(LedgerEntryTag.ledger_id == allocation["ledger_id"]).values(tag_id=2))
    service.db.commit()
    before = [dict(value) for value in service.db.execute(select(LedgerEntryTag.__table__)
        .where(LedgerEntryTag.ledger_id == allocation["ledger_id"])).mappings()]
    current = install(service, rows, choices)
    disclosure = batch(service, current, list(rows))
    assert disclosure["budget"]["tag_changes"] == 2
    result = confirm_pair(service, current, list(rows), disclosure)
    after = [dict(value) for value in service.db.execute(select(LedgerEntryTag.__table__)
        .where(LedgerEntryTag.ledger_id == allocation["ledger_id"])).mappings()]
    assert after == before
    record = result["processed_rows"][0]
    for ledger_id in [record["created_ledger_id"], *record["effective_ledger_ids"]]:
        assert service.db.execute(select(LedgerEntryTag.tag_id)
            .where(LedgerEntryTag.ledger_id == ledger_id)).scalars().all() == [1]


def test_one_canonical_b_cannot_mix_real_and_duplicate_choices(service):
    id, _rows, _choices = existing_pair(service)
    rows = prepare(service.mapper, [other_row(n, reference="same-B") for n in (1, 2)], sha="c" * 64)
    first, second = sorted(rows)
    before = manifest(service)
    with pytest.raises(TargetIntakeError, match="INVALID_DUPLICATE"):
        install(service, rows, {first: decision(dict(kind="FACT", transaction_id=id)),
            second: dict(decision="ACCEPT", resolution="NEW")})
    assert manifest(service) == before


@pytest.mark.parametrize("changed", [False, True])
def test_compound_budget_binds_disabled_rule_and_rewinds_it_in_the_same_commit(service, changed):
    service.db.add(TargetTagView(id=1, name="Mock purpose", system_name="mock-purpose", status="ACTIVE"))
    service.db.add(TargetTag(id=1, view_id=1, name="Mock unclassified", system_name="unclassified", status="ACTIVE"))
    service.db.add(AutoTagRule(id=1, name="Mock disabled rule", view_id=1, method=1,
        method_config_json='{"schema_version":1,"model_id":9,"prompt":"Mock"}',
        enabled=0, cron="", amount_mode=1, scan_after_ledger_id=100, scan_epoch=1, rule_revision=1))
    service.db.commit()
    _id, rows, choices = existing_pair(service)
    current = install(service, rows, choices)
    disclosure = batch(service, current, list(rows))
    assert disclosure["effects"]["tag_effect"]["projected_assignment_count"] == 2
    assert disclosure["effects"]["tag_effect"]["affected_rule_ids"] == [1]
    assert disclosure["budget"]["tag_changes"] == 3
    if changed:
        service.db.execute(update(AutoTagRule).where(AutoTagRule.id == 1).values(scan_epoch=2))
        service.db.commit()
        before = manifest(service)
        with pytest.raises(TargetIntakeError, match="STALE_PREVIEW"):
            confirm_pair(service, current, list(rows), disclosure)
        assert manifest(service) == before
    else:
        result = confirm_pair(service, current, list(rows), disclosure)
        rule = service.db.get(AutoTagRule, 1)
        assert (rule.scan_epoch, rule.scan_after_ledger_id, rule.rule_revision) == (2, 1, 1)
        assert rule.enabled == 0 and result["duplicate_fact_count"] == 1
