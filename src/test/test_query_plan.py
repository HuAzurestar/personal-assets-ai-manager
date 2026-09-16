from sqlalchemy import create_engine, text

from backend.core.target_database import TargetBase, ensure_target_schema


def _target_engine():
    engine = create_engine("sqlite:///:memory:")
    TargetBase.metadata.create_all(bind=engine)
    ensure_target_schema(bind=engine)
    return engine


def _plan(engine, sql: str, parameters: dict | None = None) -> str:
    with engine.connect() as connection:
        rows = connection.execute(
            text(f"EXPLAIN QUERY PLAN {sql}"),
            parameters or {},
        ).all()
    return "\n".join(str(row) for row in rows)


def test_ledger_page_uses_hot_time_index():
    engine = _target_engine()
    plan = _plan(
        engine,
        "SELECT id, occurred_time FROM ledger_entry "
        "WHERE occurred_time >= :start ORDER BY occurred_time, id LIMIT 100",
        {"start": "2025-01-01 00:00:00"},
    )
    assert "ix_ledger_entry_occurred_time_id" in plan


def test_fact_period_lookup_uses_time_index():
    engine = _target_engine()
    plan = _plan(
        engine,
        "SELECT id, occurred_time FROM bill_fact "
        "WHERE occurred_time >= :start AND occurred_time < :end "
        "ORDER BY occurred_time, id",
        {"start": "2025-01-01 00:00:00", "end": "2025-02-01 00:00:00"},
    )
    assert "ix_bill_fact_occurred_time_id" in plan


def test_raw_evidence_batch_lookup_uses_bill_index():
    engine = _target_engine()
    plan = _plan(
        engine,
        "SELECT id, bill_id, raw_payload FROM bill_raw "
        "WHERE bill_id IN (1, 2, 3) ORDER BY bill_id, id",
    )
    assert "ix_bill_raw_bill_id_id" in plan


def test_raw_reference_lookup_uses_partial_reference_index():
    engine = _target_engine()
    plan = _plan(
        engine,
        "SELECT bill_id, source_reference FROM bill_raw "
        "WHERE source_reference IN ('A', 'B') AND source_reference <> ''",
    )
    assert "ix_bill_raw_source_reference_bill_id" in plan


def test_review_lookup_from_bill_uses_implicit_id_index():
    engine = _target_engine()
    plan = _plan(
        engine,
        "SELECT review_case_id FROM review_allocation "
        "WHERE transaction_fact_id IN (1, 2, 3)",
    )
    assert "ix_review_allocation_fact_case" in plan


def test_review_lines_batch_lookup_uses_case_index():
    engine = _target_engine()
    plan = _plan(
        engine,
        "SELECT id, review_case_id, transaction_fact_id FROM review_allocation "
        "WHERE review_case_id IN (1, 2, 3) ORDER BY review_case_id, id",
    )
    assert "ix_review_allocation_case_id" in plan
