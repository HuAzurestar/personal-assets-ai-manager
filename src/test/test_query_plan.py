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
        "SELECT id, occurred_time FROM transaction_fact "
        "WHERE occurred_time >= :start AND occurred_time < :end "
        "ORDER BY occurred_time, id",
        {"start": "2025-01-01 00:00:00", "end": "2025-02-01 00:00:00"},
    )
    assert "ix_transaction_fact_occurred_time_id" in plan


def test_import_row_batch_lookup_uses_fact_index():
    engine = _target_engine()
    plan = _plan(
        engine,
        "SELECT id, transaction_fact_id, raw_payload FROM transaction_import_row "
        "WHERE transaction_fact_id IN (1, 2, 3) "
        "ORDER BY transaction_fact_id, id",
    )
    assert "ix_transaction_import_row_fact_id" in plan


def test_raw_reference_lookup_uses_partial_reference_index():
    engine = _target_engine()
    plan = _plan(
        engine,
        "SELECT transaction_fact_id, source_reference FROM transaction_import_row "
        "WHERE source_reference IN ('A', 'B') AND source_reference <> ''",
    )
    assert "ix_transaction_import_row_reference_fact" in plan


def test_review_lookup_from_fact_uses_implicit_id_index():
    engine = _target_engine()
    plan = _plan(
        engine,
        "SELECT review_id FROM review_transaction_ledger_allocation "
        "WHERE transaction_id IN (1, 2, 3)",
    )
    assert "ix_default_review_lookup" in plan or "ix_review_allocation_fact_case" in plan


def test_review_lines_batch_lookup_uses_case_index():
    engine = _target_engine()
    plan = _plan(
        engine,
        "SELECT id, review_id, transaction_id FROM review_transaction_ledger_allocation "
        "WHERE review_id IN (1, 2, 3) ORDER BY review_id, id",
    )
    assert "ix_review_allocation_case_id" in plan


def test_tag_assignment_request_hot_paths_use_indexes():
    engine = _target_engine()
    assert "ix_tag_assignment_request_scope_status" in _plan(
        engine,
        "SELECT id FROM tag_assignment_request "
        "WHERE ledger_id = 1 AND view_id = 2 AND status IN (1, 2)",
    )
    assert "ix_tag_assignment_request_rule_status" in _plan(
        engine,
        "SELECT id FROM tag_assignment_request WHERE rule_id = 1 AND status = 1",
    )
    assert "ix_tag_assignment_request_created_time_id" in _plan(
        engine,
        "SELECT id FROM tag_assignment_request ORDER BY created_time DESC, id DESC LIMIT 20",
    )
