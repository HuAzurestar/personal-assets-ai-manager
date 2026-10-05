"""Shared publication never owns the import caller's SQLite transaction."""
from datetime import datetime, timezone
from time import monotonic

import pytest
from sqlalchemy import event, select, text

from backend.core import target_database
from backend.entity import LedgerAccountRef, LedgerEntry, ReviewAllocation, ReviewCase, TransactionFact
from backend.error import TargetEconomicError
from backend.mapper.import_batch_mapper import ImportBatchMapper
from backend.schema.review_command import ReviewChangeInput
from backend.service.review_command_service import ReviewCommandService
from backend.service.target_tag_projection_service import TargetTagProjectionService
from test_pirc35_review_command import db, duplicate, normal  # noqa: F401


def manifest(db):
    return {name: [dict(row) for row in db.execute(select(table).order_by(table.c.id)).mappings()]
            for name, table in target_database.TargetBase.metadata.tables.items()}


def seed_sources(db):
    db.add_all([LedgerAccountRef(id=i, account_id=0, name=f"Mock source {i}",
        source_namespace=f"mock-{i}", source_identity=f"mock-own-{i}",
        identity_strength=1, status="ACTIVE") for i in (1, 2)])
    db.get(LedgerEntry, 1).account_ref_id = 1
    db.commit()


def create_new_b(db, owner):
    db.add(TransactionFact(id=5, fact_key="mock-new-import-b", amount=40000, currency_code="CNY",
        cash_direction=2, account_code="", occurred_time=datetime(2024, 1, 1, tzinfo=timezone.utc)))
    db.flush()
    _reviews, _positions, outputs, _legs = owner.create_initial_defaults([5], account_refs={5: 2})
    TargetTagProjectionService(db).sync_ledgers([row.id for group in outputs for row in group])


def test_read_snapshot_cannot_publish_or_promote_itself_to_write(db):
    service = ReviewCommandService(db)
    before = manifest(db)
    service.relations.read_snapshot()
    with pytest.raises(TargetEconomicError) as error:
        service.publish_in_transaction({}, owner=service.mapper)
    assert error.value.code == "WRITE_CONTEXT_INVALID"
    assert manifest(db) == before


@pytest.mark.parametrize("state", ["released", "committed", "rolled_back"])
def test_ended_write_context_cannot_be_reused(db, state):
    owner = ImportBatchMapper(db)
    service = ReviewCommandService(db)
    before = manifest(db)
    owner.begin_write()
    try:
        if state == "released":
            owner.end_write()
        elif state == "committed":
            db.commit()
        else:
            db.rollback()
        with pytest.raises(TargetEconomicError) as error:
            service.publish_in_transaction({}, owner=owner)
        assert error.value.code == "WRITE_CONTEXT_INVALID"
    finally:
        owner.end_write()
        db.rollback()
    assert manifest(db) == before


def test_other_session_write_slot_is_not_publication_authority(db):
    before = manifest(db)
    db.rollback()
    with target_database.SessionLocal() as other:
        owner = ImportBatchMapper(other)
        owner.begin_write()
        try:
            with pytest.raises(TargetEconomicError) as error:
                ReviewCommandService(db).publish_in_transaction({}, owner=owner)
            assert error.value.code == "WRITE_CONTEXT_INVALID"
        finally:
            owner.end_write()
            other.rollback()
    assert manifest(db) == before


def test_shared_publication_has_no_begin_commit_rollback_or_deadline_reset(db, monkeypatch):
    owner = ImportBatchMapper(db)
    service = ReviewCommandService(db)
    before = manifest(db)
    owner.begin_write()
    try:
        plan = service._plan(ReviewChangeInput(new_reviews=[normal(1)]))
        started, driver = owner.write_started, owner.driver
        sql = []
        def observe(_connection, _cursor, statement, _parameters, _context, _many):
            sql.append(statement)
        def forbidden(*_args, **_kwargs):
            raise AssertionError("the caller alone owns transaction boundaries")
        event.listen(db.get_bind(), "before_cursor_execute", observe)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(db, "commit", forbidden)
                patch.setattr(db, "rollback", forbidden)
                patch.setattr(owner, "begin_write", forbidden)
                patch.setattr(owner, "end_write", forbidden)
                patch.setattr(service.mapper, "begin_write", forbidden)
                patch.setattr(service.mapper, "end_write", forbidden)
                result = service.publish_in_transaction(plan, owner=owner)
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", observe)
        assert result["created_reviews"]
        assert owner.driver is driver and driver.in_transaction
        assert owner.write_started == started
        assert not any(statement.lstrip().upper().startswith(("BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT"))
                       for statement in sql)
    finally:
        owner.end_write()
        db.rollback()
    assert manifest(db) == before


def test_outer_deadline_expired_before_publication_rejects_without_writing(db):
    owner = ImportBatchMapper(db)
    service = ReviewCommandService(db)
    before = manifest(db)
    owner.begin_write()
    try:
        owner.write_started = monotonic() - 3
        with pytest.raises(TargetEconomicError) as error:
            service.publish_in_transaction({}, owner=owner)
        assert error.value.code == "WRITE_BUSY"
    finally:
        owner.end_write()
        db.rollback()
    assert manifest(db) == before


def test_publication_cannot_extend_original_deadline_by_finishing_late(db, monkeypatch):
    import backend.service.review_command_service as module
    owner, service = ImportBatchMapper(db), ReviewCommandService(db)
    before = manifest(db)
    owner.begin_write()
    try:
        plan = service._plan(ReviewChangeInput(new_reviews=[normal(1)]))
        started = owner.write_started
        def fault(stage):
            if stage == "tags":
                monkeypatch.setattr(module, "monotonic", lambda: started + 3)
        with pytest.raises(TargetEconomicError) as error:
            service.publish_in_transaction(plan, owner=owner, fault=fault)
        assert error.value.code == "WRITE_BUSY"
        assert owner.write_started == started
        assert owner.driver.in_transaction
    finally:
        owner.end_write()
        db.rollback()
    assert manifest(db) == before


@pytest.mark.parametrize("stage", ["identities", "outputs", "relations", "tags"])
def test_new_fact_default_and_duplicate_publication_roll_back_as_one_outer_unit(db, stage):
    seed_sources(db)
    before = manifest(db)
    owner, service = ImportBatchMapper(db), ReviewCommandService(db)
    owner.begin_write()
    try:
        create_new_b(db, owner)
        plan = service._plan(ReviewChangeInput(new_reviews=[duplicate(5, 1, 2)]))
        def fault(actual):
            if actual == stage:
                raise RuntimeError("fictional outer publication failure")
        with pytest.raises(RuntimeError, match="outer publication failure"):
            service.publish_in_transaction(plan, owner=owner, fault=fault)
        assert db.in_transaction() and owner.driver.in_transaction
    finally:
        owner.end_write()
        db.rollback()
    assert manifest(db) == before


def test_reader_sees_no_new_b_until_single_commit_then_only_effective_duplicate(db):
    db.execute(text("PRAGMA journal_mode=WAL"))
    db.commit()
    seed_sources(db)
    owner, service = ImportBatchMapper(db), ReviewCommandService(db)
    owner.begin_write()
    try:
        create_new_b(db, owner)
        plan = service._plan(ReviewChangeInput(new_reviews=[duplicate(5, 1, 2)]))
        with target_database.SessionLocal() as reader:
            assert reader.get(TransactionFact, 5) is None
        result = service.publish_in_transaction(plan, owner=owner)
        with target_database.SessionLocal() as reader:
            assert reader.get(TransactionFact, 5) is None
        owner.end_write()
        db.commit()
    finally:
        owner.end_write()
        db.rollback()
    with target_database.SessionLocal() as reader:
        assert reader.get(TransactionFact, 5) is not None
        allocations = [dict(row) for row in reader.execute(select(ReviewAllocation.__table__)
            .where(ReviewAllocation.transaction_id == 5)).mappings()]
        assert len(allocations) == 2
        default = reader.get(ReviewCase, next(row["review_id"] for row in allocations
            if row["review_id"] != result["created_reviews"][0]["id"]))
        assert default.behavior_type == 0 and default.status == 1
        active = reader.execute(select(LedgerEntry.entry_type, LedgerEntry.amount)
            .join(ReviewAllocation, ReviewAllocation.ledger_id == LedgerEntry.id)
            .join(ReviewCase, ReviewCase.id == ReviewAllocation.review_id)
            .where(ReviewAllocation.transaction_id == 5, ReviewCase.status == 0)).all()
        assert active == [(3, 40000)]
