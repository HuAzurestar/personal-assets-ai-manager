"""PIRC-9-only database boundary.

The production application imports only this metadata and cannot register or
create tables outside the 11-table ledger contract.
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy import create_engine

from app.config import DATABASE_URL, ensure_data_dir


ensure_data_dir()
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
    if DATABASE_URL.startswith("sqlite")
    else {},
)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


class TargetBase(DeclarativeBase):
    pass


# Register target metadata only after TargetBase exists.
from app.models import target as _target_models  # noqa: E402,F401


TARGET_TABLE_NAMES = (
    "import_file",
    "bill_raw",
    "bill_fact",
    "review_case",
    "review_case_bill",
    "review_history",
    "ledger_entry",
    "ledger_entry_source",
    "tag_view",
    "tag",
    "ledger_entry_tag",
)


# SQLAlchemy's ``Table.create(checkfirst=True)`` does not add indexes to an
# existing table. Keep this deliberately small: these are the implicit-ID and
# hot ordering paths exercised by the target mappers, not speculative indexes.
TARGET_SQLITE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_bill_raw_bill_id_id "
    "ON bill_raw (bill_id, id)",
    "CREATE INDEX IF NOT EXISTS ix_bill_raw_source_reference_bill_id "
    "ON bill_raw (source_reference, bill_id) WHERE source_reference <> ''",
    "CREATE INDEX IF NOT EXISTS ix_bill_fact_occurred_time_id "
    "ON bill_fact (occurred_time, id)",
    "CREATE INDEX IF NOT EXISTS ix_review_case_bill_bill_case "
    "ON review_case_bill (bill_id, case_id)",
    "CREATE INDEX IF NOT EXISTS ix_review_case_bill_case_id "
    "ON review_case_bill (case_id, id)",
    "CREATE INDEX IF NOT EXISTS ix_ledger_entry_source_ledger_kind_id "
    "ON ledger_entry_source (ledger_id, source_kind, source_id)",
)


def ensure_target_schema(bind=None) -> None:
    """Create/advance only the 11 PIRC-9 tables."""

    target_bind = engine if bind is None else bind
    for table_name in TARGET_TABLE_NAMES:
        TargetBase.metadata.tables[table_name].create(
            bind=target_bind,
            checkfirst=True,
        )
    if target_bind.dialect.name != "sqlite":
        return
    additions = {
        "review_case_bill": {"party": "VARCHAR(120) NOT NULL DEFAULT ''"},
        "bill_fact": {
            "account_code": "VARCHAR(120) NOT NULL DEFAULT 'UNKNOWN'",
        },
        "ledger_entry": {
            "in_account_code": "VARCHAR(120) NOT NULL DEFAULT 'UNKNOWN'",
            "out_account_code": "VARCHAR(120) NOT NULL DEFAULT 'UNKNOWN'",
        },
    }
    with target_bind.begin() as connection:
        for table_name, definitions in additions.items():
            columns = {
                item["name"]
                for item in inspect(target_bind).get_columns(table_name)
            }
            for column_name, definition in definitions.items():
                if column_name not in columns:
                    connection.execute(text(
                        f"ALTER TABLE {table_name} "
                        f"ADD COLUMN {column_name} {definition}"
                    ))
        for statement in TARGET_SQLITE_INDEXES:
            connection.execute(text(statement))


def init_target_db(bind=None) -> None:
    ensure_target_schema(bind=bind)
