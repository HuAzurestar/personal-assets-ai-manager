"""Complete exact-core risk scopes, never fuzzy merging or implicit consent."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select, update

from backend.core.import_identity import fact_values
from backend.core.import_risk import require_new_risk_confirmation
from backend.entity import TransactionFact
from backend.error import TargetIntakeError
from backend.schema.import_batch_read import ImportDuplicateHint
from pydantic import ValidationError
from backend.service.import_risk_service import ImportRiskService
from test_pirc35_import_service import service
from test_pirc35_import_batch import prepare, row, accept
from test_pirc35_import_match import paired
from test_pirc35_import_duplicate import manifest


def plan(service, rows, candidates=None, keys=None):
    candidates = candidates if candidates is not None else service.mapper.match(rows, {})
    service.db.rollback()
    return ImportRiskService(service.mapper).plan(rows, candidates, keys if keys is not None else rows)


def test_exact_empty_scope_is_typed_bounded_and_not_global_duplicate_clearance(service):
    rows = prepare(service.mapper, [row(reference="")])
    before = manifest(service)
    result = plan(service, rows)
    item = result[next(iter(rows))]
    ImportDuplicateHint(**item["hint"])
    assert item["hint"]["state"] == "NONE_IN_SCOPE" and item["hint"]["candidate_count"] == 0
    assert item["hint"]["reason_codes"] == ["EXACT_SCOPE_ONLY"]
    assert item["hint"]["scope"]["source_known"] is True
    assert len(item["premise_hash"]) == 64
    assert "0000000000123456" not in str(result)
    assert manifest(service) == before


def test_accepted_and_other_page_local_candidates_are_counted_once_per_fact(service):
    current, _key, _accepted = paired(service, 3)
    rows = service.store.get(current["token"]).rows
    later = prepare(service.mapper, [row(n, reference="") for n in range(1, 36)], sha="c" * 64)
    all_rows = rows | later
    requested = list(all_rows)[:1]
    result = plan(service, all_rows, keys=requested)
    hint = result[requested[0]]["hint"]
    assert hint["state"] == "SUSPECTED" and hint["candidate_count"] == 38
    assert set(result) == set(requested)


def test_reliable_local_evidence_rows_are_not_multiple_fact_candidates(service):
    rows = prepare(service.mapper, [row(n) for n in range(1, 36)])
    result = plan(service, rows)
    assert len(result) == 35
    assert all(item["hint"]["state"] == "NONE_IN_SCOPE" and item["hint"]["candidate_count"] == 0 for item in result.values())


def test_same_file_identical_keyless_rows_remain_separate_risks_not_merged(service):
    rows = prepare(service.mapper, [row(n, reference="") for n in (1, 2)])
    before = manifest(service)
    result = plan(service, rows)
    assert all(item["hint"]["state"] == "SUSPECTED" and item["hint"]["candidate_count"] == 1 for item in result.values())
    assert manifest(service) == before


@pytest.mark.parametrize("changes", [dict(amount_minor=-39999), dict(amount_minor=40000),
    dict(currency="CNY_4"), dict(occurred_at="2024-01-01T00:00:00.000001Z")])
def test_hint_scope_obeys_exact_core_values_not_cash_similarity(service, changes):
    original = prepare(service.mapper, [row(reference="")])
    accept(service.mapper, original)
    rows = prepare(service.mapper, [row(reference="", **changes)], sha="b" * 64)
    result = plan(service, rows)
    assert result[next(iter(rows))]["hint"]["state"] == "NONE_IN_SCOPE"


@pytest.mark.parametrize("invalid", [False, True])
def test_unchecked_scope_never_fabricates_zero_candidate_count(service, invalid):
    rows = prepare(service.mapper, [row(reference="", account={}, source_account={}, **(dict(amount_minor=0) if invalid else {}))])
    result = plan(service, rows)
    hint = result[next(iter(rows))]["hint"]
    ImportDuplicateHint(**hint)
    assert hint["state"] == "UNCHECKED" and hint["candidate_count"] is None


@pytest.mark.parametrize("state", ["SUSPECTED", "UNCHECKED"])
@pytest.mark.parametrize("choice", [dict(decision="ACCEPT"), dict(decision="ACCEPT", resolution="NEW"),
    dict(decision="ACCEPT", resolution="AUTO", acknowledge_new_risk=True),
    dict(decision="ACCEPT", resolution="NEW", acknowledge_new_risk=1)])
def test_plain_accept_or_truthy_ack_cannot_authorize_risky_new_cash(state, choice):
    with pytest.raises(TargetIntakeError) as error:
        require_new_risk_confirmation(choice, dict(state=state, candidate_count=None if state == "UNCHECKED" else 1))
    assert error.value.code == "IMPORT_REVIEW_REQUIRED"


def test_only_explicit_new_consent_or_validated_manual_path_can_proceed():
    risky = dict(state="SUSPECTED", candidate_count=2)
    require_new_risk_confirmation(dict(decision="ACCEPT", resolution="NEW", acknowledge_new_risk=True), risky)
    require_new_risk_confirmation(dict(decision="SKIP"), risky)
    require_new_risk_confirmation(dict(decision="ACCEPT"), dict(state="NONE_IN_SCOPE", candidate_count=0))
    require_new_risk_confirmation(dict(decision="ACCEPT", resolution="LINK_EXISTING"), risky)
    require_new_risk_confirmation(dict(decision="ACCEPT", resolution="DUPLICATE"), risky)
    with pytest.raises(TargetIntakeError, match="invalid import resolution"):
        require_new_risk_confirmation(dict(decision="ACCEPT", resolution="WRONG"), risky)


def test_earlier_planned_rows_acquiring_real_ids_do_not_multiply_or_change_risk_scope(service):
    rows = prepare(service.mapper, [row(n, reference="") for n in (1, 2)])
    before = plan(service, rows)
    service.db.rollback()
    accept(service.mapper, dict(list(rows.items())[:1]))
    after = plan(service, rows)
    assert after == before


def test_changed_candidate_core_changes_the_frozen_premise_without_writing(service):
    current, key, accepted = paired(service)
    rows = service.store.get(current["token"]).rows
    first = plan(service, rows)[key]
    id = accepted["processed_rows"][0]["transaction_id"]
    service.db.rollback()
    # Change through a valid independent new transaction, not corrupt old cash.
    extra = prepare(service.mapper, [row(reference="")], sha="c" * 64)
    accept(service.mapper, extra)
    second = plan(service, rows)[key]
    assert first["hint"]["candidate_count"] == 1 and second["hint"]["candidate_count"] == 2
    assert first["premise_hash"] != second["premise_hash"]


def test_repeated_signature_20k_context_has_one_sql_scope_no_quadratic_row_matching(service):
    rows = {(1, n): row(n, reference="") for n in range(1, 20001)}
    candidates = {key: dict(values=fact_values(value, "a" * 64), fact_id=0) for key, value in rows.items()}
    statements = []
    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(service.db.bind, "before_cursor_execute", observe)
    try:
        result = plan(service, rows, candidates, [next(iter(rows))])
    finally:
        event.remove(service.db.bind, "before_cursor_execute", observe)
    hint = result[next(iter(rows))]["hint"]
    assert hint["candidate_count"] == 19999
    assert len([sql for sql in statements if "FROM transaction_fact" in sql and " IN (VALUES" in sql]) == 1
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "BEGIN IMMEDIATE")) for sql in statements)


def test_distinct_401_signatures_use_two_bounded_in_batches_not_401_queries(service):
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = prepare(service.mapper, [row(n, reference="", occurred_at=(now + timedelta(minutes=n)).isoformat())
        for n in range(1, 402)])
    candidates = service.mapper.match(rows, {})
    service.db.rollback()
    queries = []
    def observe(_connection, _cursor, statement, parameters, _context, _many):
        if "FROM transaction_fact" in statement and " IN (VALUES" in statement:
            queries.append((statement, parameters))
    event.listen(service.db.bind, "before_cursor_execute", observe)
    try:
        result = plan(service, rows, candidates)
    finally:
        event.remove(service.db.bind, "before_cursor_execute", observe)
    assert len(queries) == 2
    assert [len(parameters) for _sql, parameters in queries] == [1602, 6]
    assert all(item["hint"]["state"] == "NONE_IN_SCOPE" for item in result.values())


def test_shared_candidate_limit_does_not_return_partial_hints(service, monkeypatch):
    import backend.mapper.import_batch_mapper as module
    current, key, _accepted = paired(service, 4)
    rows = service.store.get(current["token"]).rows
    candidates = service.mapper.match(rows, {})
    service.db.rollback()
    before = manifest(service)
    monkeypatch.setattr(module, "MAX_EVIDENCE_CANDIDATES", 3)
    with pytest.raises(TargetIntakeError) as error:
        plan(service, rows, candidates)
    assert error.value.code == "IMPORT_MATCH_LIMIT" and error.value.status_code == 422
    assert error.value.details["action"] == "NARROW_IMPORT_SCOPE"
    assert manifest(service) == before


@pytest.mark.parametrize("state,count", [("UNCHECKED", 0), ("NONE_IN_SCOPE", None), ("SUSPECTED", 0),
    ("SUSPECTED", -1), ("NONE_IN_SCOPE", False), ("SUSPECTED", "2")])
def test_typed_hints_cannot_claim_false_zero_or_accept_inconsistent_counts(state, count):
    with pytest.raises(ValidationError):
        ImportDuplicateHint(state=state, candidate_count=count,
            scope=dict(occurred_time=None, currency_code=None, cash_direction=None, source_known=True), reason_codes=[])
    with pytest.raises(TargetIntakeError):
        require_new_risk_confirmation(dict(decision="ACCEPT", resolution="NEW", acknowledge_new_risk=True),
            dict(state=state, candidate_count=count))


def test_caller_owned_write_deadline_is_not_extended_by_risk_reads(service, monkeypatch):
    import backend.mapper.import_batch_mapper as module
    rows = prepare(service.mapper, [row(reference="")])
    candidates = service.mapper.match(rows, {})
    service.db.rollback()
    service.mapper.begin_write()
    deadline = service.mapper.write_started
    before = manifest(service)
    monkeypatch.setattr(module, "monotonic", lambda: deadline + 3)
    try:
        with pytest.raises(TargetIntakeError) as error:
            ImportRiskService(service.mapper).plan(rows, candidates, rows)
        assert error.value.code in {"IMPORT_MATCH_LIMIT", "WRITE_BUSY"}
        assert service.mapper.write_started == deadline
        assert service.db.connection().connection.driver_connection.in_transaction
        assert manifest(service) == before
    finally:
        service.db.rollback()
        service.mapper.end_write()


@pytest.mark.parametrize("writing", [False, True])
def test_small_reads_also_check_elapsed_budget_on_exit(service, monkeypatch, writing):
    import backend.mapper.import_batch_mapper as module
    rows = prepare(service.mapper, [row(reference="")])
    candidates = service.mapper.match(rows, {})
    service.db.rollback()
    if writing:
        service.mapper.begin_write()
    original_deadline = service.mapper.write_started
    clock = [original_deadline if writing else module.monotonic()]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    signature_facts = service.mapper.signature_facts
    def finish_after_deadline(signatures):
        facts = signature_facts(signatures)
        # No expensive SQL is required to exceed the caller's Python budget.
        clock[0] += 3
        return facts
    monkeypatch.setattr(service.mapper, "signature_facts", finish_after_deadline)
    before = manifest(service)
    try:
        with pytest.raises(TargetIntakeError) as error:
            ImportRiskService(service.mapper).plan(rows, candidates, rows)
        assert error.value.code == ("WRITE_BUSY" if writing else "IMPORT_MATCH_LIMIT")
        assert service.mapper.write_started == original_deadline
        assert manifest(service) == before
        if writing:
            assert service.db.connection().connection.driver_connection.in_transaction
    finally:
        service.db.rollback()
        if writing:
            service.mapper.end_write()
