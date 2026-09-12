from sqlalchemy import create_engine

from app.database import Base


def _plan(connection, sql: str) -> list[str]:
    return [
        row[3]
        for row in connection.exec_driver_sql(f"EXPLAIN QUERY PLAN {sql}")
    ]


def _uses(plan: list[str], index_name: str) -> bool:
    return any(index_name in step for step in plan)


def test_measured_hot_paths_use_only_the_targeted_indexes(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'query-plans.db'}")
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        ledger_page = _plan(connection, """
            SELECT id
            FROM bills
            WHERE aggregate_excluded = 0
              AND occurred_at BETWEEN '2026-01-01' AND '2026-12-31'
            ORDER BY occurred_at DESC, id DESC
            LIMIT 50
        """)
        candidate_window = _plan(connection, """
            SELECT DISTINCT candidate.id
            FROM bills AS candidate
            JOIN bills AS target
              ON target.id IN (1)
             AND candidate.id != target.id
             AND candidate.occurred_at BETWEEN
                 datetime(target.occurred_at, '-5 minutes')
                 AND datetime(target.occurred_at, '+5 minutes')
             AND abs(abs(candidate.amount) - abs(target.amount)) <= 0.01
        """)
        candidate_actions = _plan(connection, """
            SELECT candidate_id, max(id)
            FROM candidate_action_logs
            WHERE candidate_id IN (1, 2, 3)
            GROUP BY candidate_id
        """)
        refund_allocations = _plan(connection, """
            SELECT refund_bill_id, expense_bill_id, amount
            FROM refund_allocations
            WHERE status = 'confirmed'
              AND (refund_bill_id = 1 OR expense_bill_id = 2)
        """)
        current_tags = _plan(connection, """
            SELECT bill_id, max(id)
            FROM tag_audits
            WHERE bill_id IN (1, 2, 3) AND superseded = 0
            GROUP BY bill_id
        """)
    engine.dispose()

    assert _uses(ledger_page, "ix_bills_occurred_at_id")
    assert not any("TEMP B-TREE FOR ORDER BY" in step for step in ledger_page)
    assert _uses(candidate_window, "ix_bills_occurred_at_id")
    assert _uses(candidate_actions, "ix_candidate_action_logs_candidate_id_id")
    assert _uses(refund_allocations, "ix_refund_allocations_refund_status_id")
    assert _uses(refund_allocations, "ix_refund_allocations_expense_status_id")
    assert _uses(current_tags, "ix_tag_audits_bill_current_id")
