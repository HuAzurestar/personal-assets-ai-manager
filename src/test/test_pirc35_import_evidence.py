"""Explicit pairs and original source proofs; fictional isolated SQLite only."""
from copy import deepcopy

import pytest
from sqlalchemy import event, insert, select, update

from backend.core.import_evidence import plan_same_source_links, target_locator
from backend.core.import_identity import raw_evidence
from backend.entity import (TransactionFact, TransactionImportFile, TransactionImportRow, ReviewCase, LedgerEntry,
                            ReviewAllocation, LedgerEntryTag, LedgerAccountRef)
from backend.error import TargetEconomicError, TargetIntakeError
from test_pirc35_import_batch import mapper, row, prepare, accept, count


def selection(mapper, rows):
    choices = {key: dict(decision="ACCEPT") for key in rows}
    candidates = mapper.match(rows, choices)
    files = {value["id"]: value for value in mapper.rows(
        TransactionImportFile, TransactionImportFile.id, [key[0] for key in rows])}
    return files, candidates, choices


def plan(mapper, rows, choices):
    files, candidates, _ = selection(mapper, rows)
    ids = [value["target"]["transaction_id"] for value in choices.values()
           if value.get("target", {}).get("kind") == "FACT"]
    return plan_same_source_links(rows, files, candidates, choices, mapper.evidence_targets(ids))


def fact_target(mapper):
    result = accept(mapper, prepare(mapper, [row(reference="")]))
    return result["processed_rows"][0]["transaction_id"]


def link_choice(id):
    return dict(decision="ACCEPT", resolution="LINK_EXISTING", target=dict(kind="FACT", transaction_id=id))


def test_fact_pair_uses_original_full_source_and_is_read_only(mapper):
    id = fact_target(mapper)
    rows = prepare(mapper, [row(reference="", note="richer evidence", merchant="another display")], sha="b" * 64)
    before = {entity: [dict(value) for value in mapper.db.execute(select(entity.__table__)).mappings()]
              for entity in (TransactionFact, TransactionImportRow, ReviewCase, LedgerEntry)}
    key = next(iter(rows))
    result = plan(mapper, rows, {key: link_choice(id)})
    assert result[key]["identity"] == ("FACT", id)
    assert result[key]["premise"]["account_code"] == "0000000000123456"
    assert result[key]["premise"]["originals"][0]["file_id"] != key[0]
    assert "raw_payload" not in str(result)
    for entity, original in before.items():
        assert [dict(value) for value in mapper.db.execute(select(entity.__table__)).mappings()] == original


@pytest.mark.parametrize("changes", [dict(amount_minor=-39999), dict(amount_minor=40000),
    dict(currency="CNY_4"), dict(occurred_at="2024-01-01T00:00:00.000001Z"),
    dict(account=dict(number="0000000000999999"))])
def test_same_source_pair_rejects_any_exact_core_or_full_account_difference(mapper, changes):
    id = fact_target(mapper)
    rows = prepare(mapper, [row(reference="", **changes)], sha="b" * 64)
    with pytest.raises(TargetIntakeError, match="FACT_CONFLICT"):
        plan(mapper, rows, {next(iter(rows)): link_choice(id)})
    assert count(mapper, TransactionFact) == count(mapper, LedgerEntry) == 1


def test_equal_timezone_instants_are_compared_in_six_digit_utc(mapper):
    id = fact_target(mapper)
    rows = prepare(mapper, [row(reference="", occurred_at="2024-01-01T08:00:00+08:00")], sha="b" * 64)
    assert plan(mapper, rows, {next(iter(rows)): link_choice(id)})


@pytest.mark.parametrize("choice", [dict(account_ref_id=0), dict(account_ref_id=999), dict(decision="SKIP")])
def test_card_override_and_skip_cannot_authorize_evidence_link(mapper, choice):
    id = fact_target(mapper)
    rows = prepare(mapper, [row(reference="")], sha="b" * 64)
    with pytest.raises(TargetIntakeError, match="INVALID_EVIDENCE_TARGET"):
        plan(mapper, rows, {next(iter(rows)): link_choice(id) | choice})


def test_same_file_keyless_rows_stay_separate_and_no_accepted_row_redecision(mapper):
    rows = prepare(mapper, [row(1, reference=""), row(2, reference="")])
    first, second = sorted(rows)
    id = accept(mapper, {first: rows[first]})["processed_rows"][0]["transaction_id"]
    with pytest.raises(TargetIntakeError, match="INVALID_EVIDENCE_TARGET"):
        plan(mapper, {second: rows[second]}, {second: link_choice(id)})
    with pytest.raises(TargetIntakeError, match="ROWS_ALREADY_PROCESSED"):
        plan(mapper, {first: rows[first]}, {first: link_choice(id)})
    assert count(mapper, TransactionFact) == 1


def test_reliable_business_key_cannot_be_overridden_by_manual_link(mapper):
    id = fact_target(mapper)
    rows = prepare(mapper, [row(reference="reliable independent key")], sha="b" * 64)
    with pytest.raises(TargetIntakeError, match="INVALID_EVIDENCE_TARGET"):
        plan(mapper, rows, {next(iter(rows)): link_choice(id)})


def test_one_file_multiple_identical_rows_cannot_broadcast_target(mapper):
    id = fact_target(mapper)
    rows = prepare(mapper, [row(n, reference="") for n in (1, 2)], sha="b" * 64)
    with pytest.raises(TargetIntakeError, match="IDENTITY_AMBIGUOUS"):
        plan(mapper, rows, {key: link_choice(id) for key in rows})


def test_different_files_can_each_explicitly_supply_one_evidence_to_same_target(mapper):
    id = fact_target(mapper)
    rows = prepare(mapper, [row(reference="")], sha="b" * 64)
    rows.update(prepare(mapper, [row(reference="")], sha="c" * 64))
    assert len(plan(mapper, rows, {key: link_choice(id) for key in rows})) == 2


def local_rows(mapper):
    anchors = prepare(mapper, [row(reference="")])
    links = prepare(mapper, [row(reference="")], sha="b" * 64)
    rows = anchors | links
    anchor, source = next(iter(anchors)), next(iter(links))
    choices = {anchor: dict(decision="ACCEPT", resolution="NEW"), source: dict(decision="ACCEPT",
        resolution="LINK_EXISTING", target=dict(kind="ROW", file_id=anchor[0], source_row_number=anchor[1]))}
    return rows, choices, anchor, source


def test_new_row_anchor_is_stable_locator_not_guessed_positive_fact_id(mapper):
    rows, choices, anchor, source = local_rows(mapper)
    original = deepcopy((rows, choices))
    result = plan(mapper, rows, choices)
    assert result[source]["locator"] == anchor and result[source]["kind"] == "ROW"
    assert result[source]["identity"][0] == "NEW"
    assert "transaction_id" not in result[source]["premise"]
    assert (rows, choices) == original
    assert count(mapper, TransactionFact) == count(mapper, LedgerEntry) == 0


@pytest.mark.parametrize("resolution,decision", [("AUTO", "SKIP"), ("LINK_EXISTING", "ACCEPT"),
                                                  ("DUPLICATE", "ACCEPT")])
def test_row_target_cannot_be_skipped_linked_or_duplicate_anchor(mapper, resolution, decision):
    rows, choices, anchor, source = local_rows(mapper)
    choices[anchor] = dict(decision=decision, resolution=resolution)
    with pytest.raises(TargetIntakeError, match="INVALID_EVIDENCE_TARGET"):
        plan(mapper, rows, choices)


def test_row_anchor_must_be_in_exact_selected_batch_and_not_self(mapper):
    rows, choices, anchor, source = local_rows(mapper)
    with pytest.raises(TargetIntakeError, match="INVALID_EVIDENCE_TARGET"):
        plan(mapper, {source: rows[source]}, {source: choices[source]})
    choices[source]["target"] = dict(kind="ROW", file_id=source[0], source_row_number=source[1])
    with pytest.raises(TargetIntakeError, match="INVALID_EVIDENCE_TARGET"):
        plan(mapper, rows, choices)


def test_existing_fact_cannot_be_disguised_as_new_row_anchor(mapper):
    rows, choices, anchor, source = local_rows(mapper)
    accept(mapper, {anchor: rows[anchor]})
    with pytest.raises(TargetIntakeError, match="INVALID_EVIDENCE_TARGET"):
        plan(mapper, rows, choices)


@pytest.mark.parametrize("target", [None, {}, dict(kind="UNKNOWN", transaction_id=1),
    dict(kind="FACT", transaction_id=True), dict(kind="FACT", transaction_id="1"),
    dict(kind="FACT", transaction_id=0), dict(kind="FACT", transaction_id=2**63),
    dict(kind="FACT", transaction_id=1, file_id=2),
    dict(kind="ROW", file_id=1, source_row_number=False),
    dict(kind="ROW", file_id=1, source_row_number=1, transaction_id=2)])
def test_strict_target_shape_and_integer_identity(target):
    with pytest.raises(TargetIntakeError, match="INVALID_EVIDENCE_TARGET"):
        target_locator(target)


def append_evidence(mapper, id, *, source_type="ccb", sha="b" * 64, reference="", number=1):
    normalized = row(number, reference=reference, source_type=source_type)
    mapper.db.rollback()
    mapper.begin_write()
    file = mapper.prepare_files("evidence-fixture", [dict(filename="fictional.csv", sha256=sha)])[sha]
    mapper.persist_parse([dict(file_id=file["id"], sha256=sha, source_type=source_type, format="csv", rows=[normalized])])
    raw_hash, payload = raw_evidence(normalized, {})
    mapper.db.execute(insert(TransactionImportRow).values(transaction_import_file_id=file["id"],
        source_row_number=number, transaction_fact_id=id, source_reference=reference,
        raw_hash=raw_hash, raw_payload=payload, row_status=1))
    mapper.db.commit()
    mapper.end_write()
    return file["id"]


def test_manual_supplement_does_not_become_original_source_authority(mapper):
    id = fact_target(mapper)
    file_id = append_evidence(mapper, id)
    result = mapper.evidence_targets([id])[id]
    assert result["issue"] is None
    assert file_id not in result["origin_file_ids"]
    assert len(result["premise"]["originals"]) == 1
    assert len(result["premise"]["evidence"]) == 2


def test_another_batch_cannot_fold_second_keyless_row_in_already_linked_file(mapper):
    id = fact_target(mapper)
    file_id = append_evidence(mapper, id)
    rows = {(file_id, 2): row(2, reference="")}
    with pytest.raises(TargetIntakeError, match="IDENTITY_AMBIGUOUS"):
        plan(mapper, rows, {(file_id, 2): link_choice(id)})


def test_reliable_local_anchor_group_includes_all_its_original_files(mapper):
    anchors = prepare(mapper, [row(reference="shared key")])
    second = prepare(mapper, [row(1, reference="shared key"), row(2, reference="")], sha="b" * 64)
    rows = anchors | second
    anchor = next(iter(anchors))
    source = next(key for key in second if key[1] == 2)
    choices = {key: dict(decision="ACCEPT") for key in rows}
    choices[source] = dict(decision="ACCEPT", resolution="LINK_EXISTING",
        target=dict(kind="ROW", file_id=anchor[0], source_row_number=anchor[1]))
    with pytest.raises(TargetIntakeError, match="INVALID_EVIDENCE_TARGET"):
        plan(mapper, rows, choices)


def test_conflicting_source_supplement_does_not_grant_cross_source_link_permission(mapper):
    id = fact_target(mapper)
    append_evidence(mapper, id, source_type="abc")
    assert mapper.evidence_targets([id])[id]["issue"] == "FACT_CONFLICT"


def test_unprovable_legacy_key_is_reported_and_never_rewritten(mapper):
    id = fact_target(mapper)
    mapper.db.execute(update(TransactionFact).where(TransactionFact.id == id).values(fact_key="immutable legacy key"))
    mapper.db.commit()
    assert mapper.evidence_targets([id])[id]["issue"] == "SOURCE_IDENTITY_REQUIRED"
    assert mapper.db.scalar(select(TransactionFact.fact_key).where(TransactionFact.id == id)) == "immutable legacy key"


@pytest.mark.parametrize("damage", ["raw_hash", "source_reference", "file_source", "missing_file", "core"])
def test_broken_original_evidence_is_not_filtered_out_as_ineligible(mapper, damage):
    id = fact_target(mapper)
    if damage == "file_source":
        mapper.db.execute(update(TransactionImportFile).values(source_type=202))
    elif damage == "core":
        mapper.db.execute(update(TransactionFact).values(amount=1))
    else:
        values = {"raw_hash": dict(raw_hash="0" * 64), "source_reference": dict(source_reference="corrupt reference"),
                  "missing_file": dict(transaction_import_file_id=99999)}[damage]
        mapper.db.execute(update(TransactionImportRow).values(**values))
    mapper.db.commit()
    with pytest.raises(TargetIntakeError, match="RELATION_BROKEN"):
        mapper.evidence_targets([id])


def test_missing_fact_and_fact_without_origin_are_explicitly_unprovable(mapper):
    id = fact_target(mapper)
    mapper.db.execute(update(TransactionImportRow).values(row_status=2, transaction_fact_id=0))
    mapper.db.commit()
    result = mapper.evidence_targets([id, id + 1])
    assert result[id]["issue"] == "SOURCE_IDENTITY_REQUIRED"
    assert result[id + 1]["issue"] == "INVALID_EVIDENCE_TARGET"


def test_target_source_reads_are_batched_for_one_and_hundred_facts(mapper):
    result = accept(mapper, prepare(mapper, [row(n, reference="") for n in range(1, 101)]))
    ids = [value["transaction_id"] for value in result["processed_rows"]]
    sizes = []
    for selected in (ids[:1], ids):
        statements = []
        def capture(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)
        event.listen(mapper.db.bind, "before_cursor_execute", capture)
        try:
            proofs = mapper.evidence_targets(selected)
        finally:
            event.remove(mapper.db.bind, "before_cursor_execute", capture)
        assert len(proofs) == len(selected) and all(value["issue"] is None for value in proofs.values())
        assert all("SELECT *" not in statement.upper() for statement in statements)
        sizes.append(len(statements))
    assert sizes == [2, 2]


def test_source_budget_rejects_whole_target_proof_not_truncated_success(mapper, monkeypatch):
    import backend.mapper.import_batch_mapper as module
    id = fact_target(mapper)
    append_evidence(mapper, id, sha="b" * 64)
    append_evidence(mapper, id, sha="c" * 64)
    monkeypatch.setattr(module, "MAX_EVIDENCE_CANDIDATES", 2)
    with pytest.raises(TargetIntakeError, match="IMPORT_MATCH_LIMIT"):
        mapper.evidence_targets([id])


def test_multiple_reliable_original_exports_are_all_proven_not_first_or_latest(mapper):
    first = accept(mapper, prepare(mapper, [row()]))
    id = first["processed_rows"][0]["transaction_id"]
    accept(mapper, prepare(mapper, [row()], sha="b" * 64))
    target = mapper.evidence_targets([id])[id]
    assert target["issue"] is None
    assert len(target["origin_file_ids"]) == len(target["premise"]["originals"]) == 2


def test_fact_link_is_written_with_evidence_only_and_unchanged_financial_rows(mapper):
    id = fact_target(mapper)
    rows = prepare(mapper, [row(reference="", note="additional original evidence")], sha="b" * 64)
    financial = (TransactionFact, ReviewCase, LedgerEntry, ReviewAllocation, LedgerEntryTag, LedgerAccountRef)
    before = {entity: [dict(value) for value in mapper.db.execute(select(entity.__table__)).mappings()] for entity in financial}
    result = accept(mapper, rows, {next(iter(rows)): link_choice(id)})
    assert result["new_fact_count"] == 0 and result["linked_existing_count"] == result["manual_linked_count"] == 1
    outcome = result["processed_rows"][0]
    assert outcome["transaction_id"] == id and outcome["resolution_effect"] == "EVIDENCE_ONLY"
    assert outcome["created_review_id"] == outcome["created_ledger_id"] == 0
    assert len(outcome["effective_review_ids"]) == len(outcome["effective_ledger_ids"]) == 1
    for entity in financial:
        assert [dict(value) for value in mapper.db.execute(select(entity.__table__)).mappings()] == before[entity]
    assert count(mapper, TransactionImportRow) == 2
    assert result["remaining_count"] == 0


def test_local_anchor_uses_actual_generated_id_once_even_with_source_order_reversed(mapper):
    # Non-contiguous accepted IDs rule out assuming next row number is Fact ID.
    id = fact_target(mapper)
    mapper.db.execute(update(TransactionFact).where(TransactionFact.id == id).values(id=47))
    mapper.db.execute(update(ReviewAllocation).values(transaction_fact_id=47))
    mapper.db.execute(update(TransactionImportRow).values(transaction_fact_id=47))
    mapper.db.commit()
    anchors = prepare(mapper, [row(reference="", amount_minor=-12345)], sha="c" * 64)
    links = prepare(mapper, [row(reference="", amount_minor=-12345)], sha="b" * 64)
    anchor, source = next(iter(anchors)), next(iter(links))
    rows = links | anchors
    choices = {anchor: dict(decision="ACCEPT", resolution="NEW"), source: dict(decision="ACCEPT",
        resolution="LINK_EXISTING", target=dict(kind="ROW", file_id=anchor[0], source_row_number=anchor[1]))}
    result = accept(mapper, rows, choices)
    assert result["new_fact_count"] == result["manual_linked_count"] == 1 and result["linked_existing_count"] == 0
    outcomes = {(value["file_id"], value["source_row_number"]): value for value in result["processed_rows"]}
    assert outcomes[source]["transaction_id"] == outcomes[anchor]["transaction_id"] > 47
    assert outcomes[source]["resolution_effect"] == "EVIDENCE_ONLY"
    assert outcomes[source]["created_review_id"] == outcomes[source]["created_ledger_id"] == 0
    assert outcomes[anchor]["resolution_effect"] == "NEW_REAL"
    assert count(mapper, TransactionFact) == count(mapper, ReviewCase) == count(mapper, LedgerEntry) == 2
    assert count(mapper, TransactionImportRow) == 3


def test_same_source_evidence_does_not_restore_inactive_original_default(mapper):
    id = fact_target(mapper)
    mapper.db.execute(update(ReviewCase).values(status=1))
    mapper.db.commit()
    rows = prepare(mapper, [row(reference="")], sha="b" * 64)
    result = accept(mapper, rows, {next(iter(rows)): link_choice(id)})
    outcome = result["processed_rows"][0]
    assert result["new_fact_count"] == 0 and outcome["effective_review_ids"] == outcome["effective_ledger_ids"] == []
    assert mapper.db.scalar(select(ReviewCase.status)) == 1
    assert count(mapper, LedgerEntry) == 1


def test_link_reupload_reads_persisted_pointer_without_extra_fact_or_default(mapper):
    id = fact_target(mapper)
    rows = prepare(mapper, [row(reference="")], sha="b" * 64)
    key = next(iter(rows))
    accept(mapper, rows, {key: link_choice(id)})
    reupload = prepare(mapper, [row(reference="")], sha="b" * 64)
    candidates = mapper.match(reupload, {})
    assert candidates[key]["classification"] == "PROCESSED" and candidates[key]["fact_id"] == id
    with pytest.raises(TargetIntakeError, match="ROWS_ALREADY_PROCESSED"):
        accept(mapper, reupload, {key: link_choice(id)})
    assert count(mapper, TransactionFact) == count(mapper, LedgerEntry) == 1
    third = prepare(mapper, [row(reference="")], sha="c" * 64)
    assert mapper.match(third, {})[next(iter(third))]["classification"] == "NEW"


def test_linked_row_in_later_batch_cannot_collapse_second_row_same_file(mapper):
    id = fact_target(mapper)
    rows = prepare(mapper, [row(1, reference=""), row(2, reference="")], sha="b" * 64)
    first, second = sorted(rows)
    accept(mapper, {first: rows[first]}, {first: link_choice(id)})
    with pytest.raises(TargetIntakeError, match="IDENTITY_AMBIGUOUS"):
        accept(mapper, {second: rows[second]}, {second: link_choice(id)})
    assert count(mapper, TransactionFact) == count(mapper, LedgerEntry) == 1
    assert mapper.progress([first[0]])[0]["remaining"] == 1


@pytest.mark.parametrize("stage", ["facts", "defaults", "tags", "source_rows", "file_counts"])
def test_new_anchor_and_link_roll_back_together_at_every_write_stage(mapper, stage):
    rows, choices, anchor, source = local_rows(mapper)
    def fault(current):
        if current == stage:
            raise RuntimeError("fictional stage failure")
    with pytest.raises(RuntimeError, match="fictional stage failure"):
        accept(mapper, rows, choices, fault=fault)
    for entity in (TransactionFact, ReviewCase, LedgerEntry, ReviewAllocation, TransactionImportRow, LedgerAccountRef):
        assert count(mapper, entity) == 0
    assert all(file["remaining"] == 1 for file in mapper.progress([anchor[0], source[0]]))


def test_new_cannot_override_existing_reliable_business_key(mapper):
    accept(mapper, prepare(mapper, [row()]))
    rows = prepare(mapper, [row()], sha="b" * 64)
    with pytest.raises(TargetIntakeError, match="INVALID_EVIDENCE_TARGET"):
        accept(mapper, rows, {next(iter(rows)): dict(decision="ACCEPT", resolution="NEW")})
    assert count(mapper, TransactionFact) == 1


@pytest.mark.parametrize("ids", [[1, True], [False], [2**63], ["1"], [-1]])
def test_target_proof_ids_are_strict_before_deduplication_or_sql(mapper, ids):
    with pytest.raises(TargetIntakeError, match="INVALID_EVIDENCE_TARGET"):
        mapper.evidence_targets(ids)


def test_target_proof_scope_has_no_unbounded_fact_or_empty_query(mapper):
    assert mapper.evidence_targets([]) == {}
    with pytest.raises(TargetIntakeError, match="DETAIL_LIMIT"):
        mapper.evidence_targets(range(1, 2002))


def test_allocation_projection_limit_rejects_instead_of_returning_partial_ids(mapper):
    result = accept(mapper, prepare(mapper, [row(n, reference="") for n in range(1, 4)]))
    ids = [value["transaction_id"] for value in result["processed_rows"]]
    assert len(mapper.allocations_for_facts(ids, active=True, limit=3)) == 3
    with pytest.raises(TargetEconomicError, match="immutable relation budget exceeded"):
        mapper.allocations_for_facts(ids, active=True, limit=2)


@pytest.mark.parametrize("choice", [dict(decision="ACCEPT", resolution="UNKNOWN"),
    dict(decision="ACCEPT", resolution="AUTO", target=dict(kind="FACT", transaction_id=1)),
    dict(decision="SKIP", resolution="NEW")])
def test_internal_choice_cannot_silently_degrade_to_ordinary_accept(mapper, choice):
    rows = prepare(mapper, [row(reference="")])
    with pytest.raises(TargetIntakeError, match="INVALID_EVIDENCE_TARGET"):
        accept(mapper, rows, {next(iter(rows)): choice})
    assert count(mapper, TransactionFact) == count(mapper, TransactionImportRow) == 0
