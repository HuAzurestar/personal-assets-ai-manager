from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from backend.core import target_database
from backend.entity import (LedgerAccountParty, LedgerEntry, Position, PositionLeg, ReviewAllocation,
                            ReviewCase, ReviewLedgerPositionLegAllocation, ReviewRevision, TransactionFact)
from backend.error import TargetEconomicError
from backend.mapper.review_command_mapper import ReviewCommandMapper
from backend.schema.review_command import ReviewChangeInput, ReviewCommandInput
from backend.service.review_command_service import ReviewCommandService, canonical


@pytest.fixture
def db():
    target_database.init_target_db()
    with target_database.SessionLocal() as session:
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        session.add(LedgerAccountParty(id=1, name="Mock person", status="ACTIVE"))
        session.add_all([TransactionFact(id=i, fact_key=f"mock-{i}", occurred_time=now,
                        cash_direction=2 if i != 2 else 1, amount=amount, currency_code="CNY", account_code="")
                        for i, amount in enumerate((40000, 30000, 10000, 20000), 1)])
        session.flush()
        ReviewCommandMapper(session).create_initial_defaults([1, 2, 3, 4])
        session.commit()
        yield session


def normal(*ids):
    return dict(case_code="NORMAL", title="Mock manual", parameters=dict(transaction_ids=list(ids)))


def execute(db, **change):
    service = ReviewCommandService(db)
    intent = ReviewChangeInput(**change)
    preview = service.preview(intent)
    assert preview["blocking_issues"] == [], preview
    return service.command(ReviewCommandInput(**change, expected_reviews=preview["expected_reviews"],
                                              preview_digest=preview["preview_digest"]))


def counts(db):
    return tuple(db.scalar(select(func.count()).select_from(entity)) for entity in
                 (ReviewCase, LedgerEntry, ReviewAllocation, Position, PositionLeg,
                  ReviewLedgerPositionLegAllocation, ReviewRevision))


def test_publish_duration_records_both_success_and_failure_without_business_labels(db):
    from backend.core.feature_observability import observability
    service = ReviewCommandService(db)
    change = dict(new_reviews=[normal(1)])
    preview = service.preview(ReviewChangeInput(**change))
    command = ReviewCommandInput(**change, expected_reviews=preview["expected_reviews"], preview_digest=preview["preview_digest"])
    before = counts(db)

    def fail(stage):
        if stage == "tags":
            raise RuntimeError("fictional private ledger amount")

    with pytest.raises(RuntimeError):
        service.command(command, fault=fail)
    assert counts(db) == before
    assert not any(row["name"] == "tag_invalidated" for row in observability.snapshot()["metrics"])
    result = service.command(command)
    assert result["created_reviews"]
    metrics = {row["name"]: row for row in observability.snapshot()["metrics"]}
    assert metrics["publish_duration_ms"]["count"] == 2
    assert metrics["tag_invalidated"]["total"] == 1
    assert "private ledger" not in str(observability.snapshot())


def test_preview_is_read_only_and_replacement_restores_existing_defaults(db):
    before = counts(db)
    service = ReviewCommandService(db)
    assert service.preview(ReviewChangeInput(new_reviews=[normal(1, 2)]))["blocking_issues"] == []
    assert counts(db) == before
    a = execute(db, new_reviews=[normal(1, 2)])["created_reviews"][0]["id"]
    b = execute(db, new_reviews=[normal(3, 4)])["created_reviews"][0]["id"]
    original_a = service.detail(a)
    c = execute(db, new_reviews=[normal(2, 3)])["created_reviews"][0]["id"]
    assert db.get(ReviewCase, a).status == db.get(ReviewCase, b).status == 1
    assert db.get(ReviewCase, 1).status == db.get(ReviewCase, 4).status == 0
    execute(db, deactivate_review_ids=[c])
    assert all(db.get(ReviewCase, i).status == 0 for i in range(1, 5))
    assert db.get(ReviewCase, a).status == db.get(ReviewCase, b).status == 1
    revoked_a = service.detail(a)
    for key in ("allocations", "ledger_entries", "position_legs", "position_allocations", "title", "created_time"):
        assert revoked_a[key] == original_a[key]
    assert counts(db)[-1] == 0  # no revision or replay receipt


def test_one_hundred_activation_cycles_do_not_grow_business_rows(db):
    rid = execute(db, new_reviews=[normal(1)])["created_reviews"][0]["id"]
    before = counts(db)
    for _ in range(100):
        execute(db, deactivate_review_ids=[rid])
        execute(db, activate_review_ids=[rid])
    assert counts(db) == before


def test_default_cannot_be_disabled_without_replacement(db):
    preview = ReviewCommandService(db).preview(ReviewChangeInput(deactivate_review_ids=[1]))
    assert preview["blocking_issues"][0]["code"] == "DEFAULT_DEACTIVATION_FORBIDDEN"


@pytest.mark.parametrize("stage", ["identities", "outputs", "relations", "tags"])
def test_every_publication_stage_rolls_back(db, stage):
    service = ReviewCommandService(db)
    change = dict(new_reviews=[normal(1)])
    preview = service.preview(ReviewChangeInput(**change))
    before = counts(db)
    def fault(actual):
        if actual == stage:
            raise RuntimeError("injected")
    with pytest.raises(RuntimeError, match="injected"):
        service.command(ReviewCommandInput(**change, expected_reviews=preview["expected_reviews"],
                        preview_digest=preview["preview_digest"]), fault=fault)
    assert counts(db) == before
    assert db.get(ReviewCase, 1).status == 0


def test_post_commit_failure_is_unknown_not_replay_or_rollback(db):
    service = ReviewCommandService(db)
    change = dict(new_reviews=[normal(1)])
    preview = service.preview(ReviewChangeInput(**change))
    def fault(stage):
        if stage == "response":
            raise RuntimeError("response lost")
    command = ReviewCommandInput(**change, expected_reviews=preview["expected_reviews"], preview_digest=preview["preview_digest"])
    with pytest.raises(TargetEconomicError) as error:
        service.command(command, fault=fault)
    assert error.value.code == "RESULT_UNKNOWN"
    assert counts(db)[0] == 5
    assert db.get(ReviewCase, 1).status == 1
    with pytest.raises(TargetEconomicError) as stale:
        service.command(command)
    assert stale.value.code == "ENTITY_CHANGED"
    assert counts(db)[0] == 5


def test_stale_preview_rolls_back_before_creating_outputs(db):
    service = ReviewCommandService(db)
    change = dict(new_reviews=[normal(1)])
    preview = service.preview(ReviewChangeInput(**change))
    execute(db, new_reviews=[normal(1)])
    before = counts(db)
    with pytest.raises(TargetEconomicError) as error:
        service.command(ReviewCommandInput(**change, expected_reviews=preview["expected_reviews"], preview_digest=preview["preview_digest"]))
    assert error.value.code == "ENTITY_CHANGED"
    assert counts(db) == before


def borrowed(source=0, position=None):
    opening = position is None
    return dict(case_code="BORROW_REPAY", title="Mock borrowing", parameters=dict(
        new_positions=[dict(title="Mock note", description="fictional", type="ASSET", usage_scenario="PERSONAL-LENDING",
                            party_id=1, counterparty="Mock borrower", unit_code="CNY")] if opening else [],
        allocations=[dict(transaction_id=1 if opening else 2, economic_type="ASSET_LIABILITY",
                          cash_amount=40000 if opening else 30000, account_ref_id=0)],
        legs=[dict(**({"new_position_index": 0} if opening else {"existing_position_id": position}),
                   type="MOVEMENT", leg_amount=40000 if opening else 30000, leg_direction="IN" if opening else "OUT",
                   occurred_time="2024-01-01T00:00:00Z", source=source, basis="Mock statement")],
        position_allocations=[dict(allocation_index=0, leg_index=0, cash_amount=40000 if opening else 30000, cash_currency_code="CNY")]))


def test_borrow_400_collect_300_preserves_evidence_and_source_invalidation(db):
    service = ReviewCommandService(db)
    result = execute(db, new_reviews=[borrowed()])
    rid, pid = result["created_reviews"][0]["id"], result["created_positions"][0]["id"]
    source = service.detail(rid)["position_legs"][0]["id"]
    repayment = execute(db, new_reviews=[borrowed(source, pid)])["created_reviews"][0]["id"]
    rows = db.scalars(select(PositionLeg).where(PositionLeg.position_id == pid)).all()
    assert sum(row.leg_amount * (1 if row.leg_direction == "IN" else -1) for row in rows) == 10000
    assert service.detail(repayment)["type"] == "BORROW_AND_REPAY"
    stopped = execute(db, deactivate_review_ids=[rid])
    assert stopped["consumer_state"]["position_states"] == [dict(position_id=pid, quantity_state="NEEDS_REVIEW", quantity=None)]
    assert source not in stopped["consumer_state"]["dependent_position_leg_ids"]
    assert service.detail(repayment)["position_legs"][0]["id"] in stopped["consumer_state"]["dependent_position_leg_ids"]
    assert db.get(ReviewCase, repayment).status == 0
    assert counts(db)[0:3] == (6, 6, 6)


def test_changed_position_metadata_invalidates_digest(db):
    service = ReviewCommandService(db)
    result = execute(db, new_reviews=[borrowed()])
    pid = result["created_positions"][0]["id"]
    source = service.detail(result["created_reviews"][0]["id"])["position_legs"][0]["id"]
    change = dict(new_reviews=[borrowed(source, pid)])
    preview = service.preview(ReviewChangeInput(**change))
    db.get(Position, pid).status = "ARCHIVED"
    db.commit()
    with pytest.raises(TargetEconomicError) as error:
        service.command(ReviewCommandInput(**change, expected_reviews=preview["expected_reviews"], preview_digest=preview["preview_digest"]))
    assert error.value.code in {"ENTITY_CHANGED", "POSITION_NOT_ACTIVE"}
    assert counts(db)[0] == 5


def test_repeated_initial_default_creation_is_rejected(db):
    before = counts(db)
    with pytest.raises(TargetEconomicError):
        ReviewCommandMapper(db).create_initial_defaults([1])
    assert counts(db) == before


def test_missing_original_default_is_reported_not_rebuilt(db):
    db.get(ReviewCase, 1).behavior_type = 4
    db.commit()
    before = counts(db)
    preview = ReviewCommandService(db).preview(ReviewChangeInput(new_reviews=[normal(1)]))
    assert preview["blocking_issues"][0]["code"] == "DEFAULT_IDENTITY_REQUIRED"
    assert counts(db) == before


def test_partial_legacy_groups_require_explicit_reorganization(db):
    mapper = ReviewCommandMapper(db)
    facts = {row["id"]: row for row in mapper.named_rows("facts", [1])}
    draft = lambda amount: dict(type=4, title="Legacy partial fixture", new_positions=[], legs=[], position_allocations=[],
                                allocations=[dict(transaction_id=1, entry_type=0, cash_amount=amount, account_ref_id=0)])
    groups, _, _, _ = mapper.publish([draft(24000), draft(16000)], facts, {1: 1}, {1: mapper.named_rows("reviews", [1])[0]})
    ids = [row.id for row in groups]
    db.commit()
    before = counts(db)
    preview = ReviewCommandService(db).preview(ReviewChangeInput(deactivate_review_ids=[ids[0]]))
    assert preview["blocking_issues"][0]["code"] == "LEGACY_COVERAGE_REVIEW_REQUIRED"
    assert counts(db) == before
    assert db.get(ReviewCase, ids[1]).status == 0
    replacement = execute(db, new_reviews=[normal(1)])
    assert not replacement["coverage"][0]["cash_amount"] - 40000


def test_explicit_duplicate_keeps_coverage_but_excludes_cash_and_restores_original(db):
    from backend.entity import LedgerAccountRef
    from backend.service.ledger_entry_service import LedgerEntryService
    from backend.schema.ledger_entry import LedgerEntrySummaryQuery
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    db.add_all([LedgerAccountRef(id=i, account_id=0, name=f"Mock source {i}", institution="", reference="",
                source_namespace=f"mock-source-{i}", source_identity=f"mock-own-{i}", identity_strength=1, status="ACTIVE") for i in (1,2)])
    db.add(TransactionFact(id=5, fact_key="mock-duplicate", occurred_time=now, cash_direction=2, amount=40000,
                           currency_code="CNY", account_code=""))
    db.get(LedgerEntry, 1).account_ref_id = 1
    db.flush()
    ReviewCommandMapper(db).create_initial_defaults([5], account_refs={5: 2})
    db.commit()
    change = dict(case_code="DUPLICATE", parameters=dict(transaction_ids=[5]),
                  account_bindings=[dict(transaction_id=5, account_ref_id=2)],
                  duplicate_transactions=[dict(transaction_id=5, kept_transaction_id=1)])
    result = execute(db, new_reviews=[change])
    rid = result["created_reviews"][0]["id"]
    detail = ReviewCommandService(db).detail(rid)
    assert detail["ledger_entries"][0]["economic_type"] == "DUPLICATE"
    assert detail["allocations"][0]["cash_amount"] == 40000
    assert detail["position_legs"] == detail["position_allocations"] == []
    assert LedgerEntryService(db).summary(LedgerEntrySummaryQuery()).totals[0].income_and_expense_out_amount == 70000
    execute(db, deactivate_review_ids=[rid])
    assert LedgerEntryService(db).summary(LedgerEntrySummaryQuery()).totals[0].income_and_expense_out_amount == 110000
    change["account_bindings"][0]["account_ref_id"] = 1
    invalid = ReviewCommandService(db).preview(ReviewChangeInput(new_reviews=[change]))
    assert invalid["blocking_issues"][0]["code"] == "INVALID_DUPLICATE"


def test_credit_purchase_and_principal_repay_use_same_liability_position(db):
    service = ReviewCommandService(db)
    opening = dict(case_code="POS_CREDIT_PURCHASE", title="Mock credit purchase", new_positions=[dict(
        title="Mock credit debt", type="LIABILITY", usage_scenario="CREDIT-CARD", party_id=1, unit_code="CNY")],
        allocations=[dict(transaction_id=1, economic_type="TRANSACTION", cash_amount=40000, account_ref_id=0)],
        legs=[dict(new_position_index=0, type="MOVEMENT", leg_amount=40000, leg_direction="IN", occurred_time="2024-01-01T00:00:00Z")],
        position_allocations=[dict(allocation_index=0, leg_index=0, cash_amount=40000, cash_currency_code="CNY")])
    result = execute(db, new_reviews=[opening])
    pid, rid = result["created_positions"][0]["id"], result["created_reviews"][0]["id"]
    original = service.detail(rid)
    assert original["type"] == "CREDIT_CARD"
    source = original["position_legs"][0]["id"]
    repayment = dict(case_code="POS_CREDIT_REPAY", title="Mock principal", new_positions=[],
        allocations=[dict(transaction_id=4, economic_type="ASSET_LIABILITY", cash_amount=20000, account_ref_id=0)],
        legs=[dict(existing_position_id=pid, type="MOVEMENT", leg_amount=20000, leg_direction="OUT", source=source,
                   occurred_time="2024-01-01T00:00:00Z")],
        position_allocations=[dict(allocation_index=0, leg_index=0, cash_amount=20000, cash_currency_code="CNY")])
    result = execute(db, new_reviews=[repayment])
    assert result["created_reviews"][0]["type"] == "CREDIT_CARD"
    rows = db.scalars(select(PositionLeg).where(PositionLeg.position_id == pid)).all()
    assert sum(row.leg_amount * (1 if row.leg_direction == "IN" else -1) for row in rows) == 20000


def test_two_preview_sessions_cannot_both_replace_the_same_current_fact(db):
    change = dict(new_reviews=[normal(1)])
    service = ReviewCommandService(db)
    preview = service.preview(ReviewChangeInput(**change))
    with target_database.SessionLocal() as other:
        other_preview = ReviewCommandService(other).preview(ReviewChangeInput(**change))
        other.rollback()
        execute(db, **change)
        with pytest.raises(TargetEconomicError) as error:
            ReviewCommandService(other).command(ReviewCommandInput(**change, expected_reviews=other_preview["expected_reviews"],
                                               preview_digest=other_preview["preview_digest"]))
        assert error.value.code == "ENTITY_CHANGED"
    assert counts(db)[0] == 5


def test_exception_at_commit_boundary_is_reported_as_unknown(db, monkeypatch):
    service = ReviewCommandService(db)
    change = dict(new_reviews=[normal(1)])
    preview = service.preview(ReviewChangeInput(**change))
    real_commit = db.commit
    def uncertain_commit():
        real_commit()
        raise RuntimeError("commit listener failed")
    monkeypatch.setattr(db, "commit", uncertain_commit)
    with pytest.raises(TargetEconomicError) as error:
        service.command(ReviewCommandInput(**change, expected_reviews=preview["expected_reviews"], preview_digest=preview["preview_digest"]))
    assert error.value.code == "RESULT_UNKNOWN"
    assert counts(db)[0] == 5
    assert db.get(ReviewCase, 1).status == 1


def test_write_lock_timeout_has_no_partial_publication(db):
    service = ReviewCommandService(db)
    change = dict(new_reviews=[normal(1)])
    preview = service.preview(ReviewChangeInput(**change))
    db.rollback()
    with target_database.engine.connect() as writer:
        writer.exec_driver_sql("BEGIN IMMEDIATE")
        with pytest.raises(TargetEconomicError) as error:
            service.command(ReviewCommandInput(**change, expected_reviews=preview["expected_reviews"], preview_digest=preview["preview_digest"]))
        assert error.value.code == "WRITE_BUSY"
        writer.rollback()
    assert counts(db)[0] == 4
