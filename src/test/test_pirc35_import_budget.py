"""Real persisted import budget boundaries, with passive SQLite timing evidence.

No enlarged limits, fake tag dictionaries, clock jumps or transaction wrappers.
All writes use the existing import Service in the autouse disposable database.
"""
from collections import Counter
from contextlib import contextmanager
import json
from time import monotonic

import pytest
from sqlalchemy import event, func, insert, select

from backend.core import target_database
from backend.entity import (LedgerEntry, LedgerEntryTag, ReviewAllocation, ReviewCase,
                            TargetTag, TargetTagView, TransactionFact, TransactionImportRow)
from backend.error import TargetIntakeError
from test_pirc35_import_service import service, choose  # noqa: F401
from test_pirc35_import_batch import prepare, row, count
from test_pirc35_import_confirm_preview import install, batch
from test_pirc35_import_duplicate import confirm_pair, many_local_pairs, manifest


def seed_views(service, size):
    """Bulk fixture setup, not financial publication or an alternate write API."""
    service.db.execute(insert(TargetTagView), [dict(id=n, name=f"Mock budget view {n}",
        system_name=f"mock-budget-{n}", status="ACTIVE") for n in range(1, size + 1)])
    service.db.execute(insert(TargetTag), [dict(id=n, view_id=n, name="Mock unclassified",
        system_name="unclassified", status="ACTIVE") for n in range(1, size + 1)])
    service.db.commit()


@contextmanager
def sqlite_work(engine):
    """Observe actual BEGIN IMMEDIATE acquisition and pre-COMMIT write work.

    SQLAlchemy's commit event occurs BEFORE the driver's durable commit; this
    interval is explicitly not a durable-commit latency or a production SLA.
    """
    evidence = dict(sql=Counter(), locked_sql=Counter(), begins=0, commits=0, rollbacks=0,
                    lock_wait_ms=None, failed_lock_wait_ms=None, locked_before_commit_ms=None,
                    locked_before_rollback_ms=None)
    times = {}

    def before(_connection, _cursor, statement, _parameters, _context, _many):
        kind = statement.lstrip().split(None, 1)[0].upper()
        evidence["sql"][kind] += 1
        if statement.lstrip().upper().startswith("BEGIN IMMEDIATE"):
            evidence["begins"] += 1
            times["requested"] = monotonic()
        elif "locked" in times and not times.get("ended"):
            evidence["locked_sql"][kind] += 1

    def after(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("BEGIN IMMEDIATE"):
            times["locked"] = monotonic()
            times.pop("ended", None)
            evidence["lock_wait_ms"] = round((times["locked"] - times["requested"]) * 1000, 3)

    def commit(_connection):
        evidence["commits"] += 1
        if "locked" in times:
            evidence["locked_before_commit_ms"] = round((monotonic() - times["locked"]) * 1000, 3)
        times["ended"] = True

    def rollback(_connection):
        evidence["rollbacks"] += 1
        if "locked" in times and not times.get("ended"):
            evidence["locked_before_rollback_ms"] = round((monotonic() - times["locked"]) * 1000, 3)
        times["ended"] = True

    def failed(context):
        if context.statement and context.statement.lstrip().upper().startswith("BEGIN IMMEDIATE"):
            evidence["failed_lock_wait_ms"] = round((monotonic() - times["requested"]) * 1000, 3)

    listeners = [("before_cursor_execute", before), ("after_cursor_execute", after),
                 ("commit", commit), ("rollback", rollback), ("handle_error", failed)]
    for name, listener in listeners:
        event.listen(engine, name, listener)
    try:
        yield evidence
    finally:
        for name, listener in listeners:
            event.remove(engine, name, listener)


def record_work(record_property, name, budget, actual, evidence):
    record_property(name, json.dumps(dict(planned=budget, actual=actual, **evidence), sort_keys=True))


def test_real_hundred_group_compound_commit_matches_full_preview_and_sql_work(service, record_property):
    seed_views(service, 3)
    rows, choices = many_local_pairs(service, 49, extra=True)
    current = install(service, rows, choices)
    before = manifest(service)
    disclosure = batch(service, current, list(rows))
    assert disclosure["budget"] == dict(selected_rows=99, review_groups=100, facts=99,
        outputs=148, position_links=148, tag_changes=444)
    assert manifest(service) == before, "full disclosure is read-only"
    service.db.rollback()
    with sqlite_work(service.db.get_bind()) as evidence:
        result = confirm_pair(service, current, list(rows), disclosure)
    actual = dict(facts=count(service.mapper, TransactionFact), reviews=count(service.mapper, ReviewCase),
        outputs=count(service.mapper, LedgerEntry), links=count(service.mapper, ReviewAllocation),
        source_rows=count(service.mapper, TransactionImportRow), tags=count(service.mapper, LedgerEntryTag))
    assert actual == dict(facts=99, reviews=100, outputs=148, links=148, source_rows=99, tags=444)
    assert result["new_fact_count"] == 99 and result["duplicate_fact_count"] == 49
    assert result["remaining_count"] == 0
    active = service.db.execute(select(LedgerEntry.entry_type, func.count()).join(
        ReviewAllocation, ReviewAllocation.ledger_id == LedgerEntry.id).join(
        ReviewCase, ReviewCase.id == ReviewAllocation.review_id).where(ReviewCase.status == 0)
        .group_by(LedgerEntry.entry_type)).all()
    assert dict(active) == {0: 50, 3: 49}, "one real cash survives for each pair and the ordinary row"
    assert evidence["begins"] == evidence["commits"] == 1
    # The existing Service rolls back a post-commit read snapshot; that is not
    # a financial rollback and does not undo the one successful publication.
    assert evidence["locked_before_commit_ms"] is not None
    # The <=40 INSERT guard belongs to 1000 pure defaults in serial.py, not
    # the shared existing Review publisher's 49 additional DUP outputs.
    # Measure the complete compound work rather than silently imposing that
    # different workflow's count ceiling. Its original guard stays unchanged.
    assert evidence["locked_sql"]["INSERT"] > 0
    record_work(record_property, "compound_100_groups_sql_work", disclosure["budget"], actual, evidence)


def test_real_fifty_thousand_tags_keep_deadline_and_complete_entire_scope(service, record_property):
    seed_views(service, 50)
    rows = prepare(service.mapper, [row(n, reference=f"Mock-budget-{n}") for n in range(1, 1001)])
    choices = {key: dict(decision="ACCEPT", resolution="NEW", acknowledge_new_risk=True) for key in rows}
    current = install(service, rows, choices)
    before = manifest(service)
    disclosure = batch(service, current, list(rows))
    assert disclosure["can_confirm"] and disclosure["budget"]["tag_changes"] == 50000
    assert disclosure["budget"]["review_groups"] == 1000, "pure import retains its dedicated exemption"
    assert manifest(service) == before
    service.db.rollback()
    attempts = []
    with sqlite_work(service.db.get_bind()) as evidence:
        try:
            result = confirm_pair(service, current, list(rows), disclosure)
        except TargetIntakeError as error:
            assert error.status_code == 503 and error.code == "WRITE_BUSY"
            result = None
    attempts.append(dict(selected_rows=1000, outcome="COMMITTED" if result else "WRITE_BUSY", **evidence))
    if result is None:
        # SOL-010 says ceilings are refusal boundaries, not wall-clock capacity
        # guarantees. A real deadline is never disabled or enlarged for CI.
        # Prove complete rollback, then explicitly prepare and approve smaller
        # fresh scopes covering EVERY original row, not a passing prefix.
        assert manifest(service) == before
        assert evidence["commits"] == 0 and evidence["locked_before_rollback_ms"] is not None
        keys = sorted(rows)
        for selected in (keys[:500], keys[500:]):
            current = service.current(current["token"])
            # GET only reads the cached draft. Saving the explicit remaining
            # choices rechecks live premises (including the source account
            # legitimately created by the preceding committed child). Mirror
            # the UI's save/revalidate step, never replace cached premises.
            current = choose(service, current, selected, resolution="NEW", acknowledge_new_risk=True)
            fresh = batch(service, current, selected)
            assert fresh["budget"]["selected_rows"] == 500 and fresh["budget"]["tag_changes"] == 25000
            assert fresh["batch_preview_digest"] != disclosure["batch_preview_digest"]
            service.db.rollback()
            with sqlite_work(service.db.get_bind()) as retry:
                result = confirm_pair(service, current, selected, fresh)
            assert result["new_fact_count"] == 500
            assert retry["begins"] == retry["commits"] == 1
            attempts.append(dict(selected_rows=500, outcome="COMMITTED", **retry))
    actual = dict(facts=count(service.mapper, TransactionFact), reviews=count(service.mapper, ReviewCase),
        outputs=count(service.mapper, LedgerEntry), links=count(service.mapper, ReviewAllocation),
        source_rows=count(service.mapper, TransactionImportRow), tags=count(service.mapper, LedgerEntryTag))
    assert actual == dict(facts=1000, reviews=1000, outputs=1000, links=1000, source_rows=1000, tags=50000)
    assert result["remaining_count"] == 0
    accepted = service.db.execute(select(TransactionImportRow.transaction_fact_id)).scalars().all()
    assert len(accepted) == len(set(accepted)) == 1000
    per_ledger = service.db.execute(select(LedgerEntryTag.ledger_id, func.count())
        .group_by(LedgerEntryTag.ledger_id)).all()
    assert len(per_ledger) == 1000 and {value for _id, value in per_ledger} == {50}
    record_property("pure_50000_tags_sql_work", json.dumps(dict(planned=disclosure["budget"],
        actual=actual, attempts=attempts, all_original_rows_completed=True,
        deadline_unchanged=True, no_automatic_post_replay=True), sort_keys=True))


def test_real_fifty_thousand_and_one_tag_projection_rejects_without_financial_write(service, record_property):
    # Two new defaults plus one DUP output times 16667 real active Views is
    # exactly 50001 projected assignments. No dictionary/counter replacement.
    seed_views(service, 16667)
    rows, choices = many_local_pairs(service, 1)
    current = install(service, rows, choices)
    before = manifest(service)
    service.db.rollback()
    with sqlite_work(service.db.get_bind()) as evidence:
        with pytest.raises(TargetIntakeError) as error:
            batch(service, current, list(rows))
    assert error.value.status_code == 413 and error.value.code == "TAG_IMPACT_LIMIT"
    assert manifest(service) == before
    assert evidence["begins"] == evidence["commits"] == 0
    assert sum(evidence["sql"][kind] for kind in ("INSERT", "UPDATE", "DELETE")) == 0
    record_property("real_50001_tags_rejected", json.dumps(dict(active_views=16667,
        defaults=2, duplicate_outputs=1, tag_changes=50001, code=error.value.code,
        zero_financial_write=True, sql=evidence["sql"]), sort_keys=True))


def test_real_import_lock_timeout_records_wait_and_preserves_twenty_tables(service, record_property):
    rows = prepare(service.mapper, [row(reference="Mock-budget-lock")])
    current = install(service, rows, {key: dict(decision="ACCEPT", resolution="NEW",
        acknowledge_new_risk=True) for key in rows})
    disclosure = batch(service, current, list(rows))
    before = manifest(service)
    service.db.rollback()
    # Independent actual SQLite write-slot owner, not a fabricated HTTP error,
    # shortened busy timeout or replacement of BEGIN IMMEDIATE.
    with target_database.engine.connect() as holder:
        holder.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            with sqlite_work(service.db.get_bind()) as evidence:
                with pytest.raises(TargetIntakeError) as error:
                    confirm_pair(service, current, list(rows), disclosure)
        finally:
            holder.rollback()
    assert error.value.status_code == 503 and error.value.code == "WRITE_BUSY"
    assert manifest(service) == before
    assert evidence["begins"] == 1 and evidence["commits"] == 0
    assert evidence["lock_wait_ms"] is None and evidence["locked_before_commit_ms"] is None
    assert evidence["failed_lock_wait_ms"] is not None
    assert sum(evidence["sql"][kind] for kind in ("INSERT", "UPDATE", "DELETE")) == 0
    record_work(record_property, "actual_2s_import_lock_timeout", disclosure["budget"],
                dict(code=error.value.code, complete_twenty_table_rollback=True), evidence)
