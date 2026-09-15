"""PIRC-9-only database boundary.

The production application imports only this metadata and cannot register or
create tables outside the target ledger contract.
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy import create_engine

from backend.core.config import DATABASE_URL, ensure_data_dir


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
from backend import entity as _target_models  # noqa: E402,F401


TARGET_TABLE_NAMES = (
    "import_file",
    "bill_raw",
    "bill_fact",
    "review_case",
    "review_case_bill",
    "review_history",
    "ledger_entry",
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
    "CREATE INDEX IF NOT EXISTS ix_review_case_bill_economic_id "
    "ON review_case_bill (economic_id, id)",
    "CREATE INDEX IF NOT EXISTS ix_ledger_entry_occurred_time_id "
    "ON ledger_entry (occurred_time, id)",
)


def ensure_target_schema(bind=None) -> None:
    """Create or advance only the target ledger tables."""

    target_bind = engine if bind is None else bind
    for table_name in TARGET_TABLE_NAMES:
        TargetBase.metadata.tables[table_name].create(
            bind=target_bind,
            checkfirst=True,
        )
    if target_bind.dialect.name != "sqlite":
        return
    additions = {
        "review_case": {
            "behavior_code": "VARCHAR(40) NOT NULL DEFAULT 'UNKNOWN'",
        },
        "review_case_bill": {
            "party": "VARCHAR(120) NOT NULL DEFAULT ''",
            "economic_id": "INTEGER NOT NULL DEFAULT 0",
            "entry_type": "INTEGER NOT NULL DEFAULT 0",
        },
        "bill_fact": {
            "account_code": "VARCHAR(120) NOT NULL DEFAULT 'UNKNOWN'",
        },
        "ledger_entry": {
            "entry_type": "INTEGER NOT NULL DEFAULT 0",
            "entry_direction": "INTEGER NOT NULL DEFAULT 0",
            "account_code": "VARCHAR(120) NOT NULL DEFAULT 'UNKNOWN'",
            "counterparty_account_ref": "VARCHAR(200) NOT NULL DEFAULT ''",
            "occurred_time": "DATETIME",
            "amount_value": "BIGINT NOT NULL DEFAULT 0",
            "amount_scale": "SMALLINT NOT NULL DEFAULT 2",
            "currency_code": "VARCHAR(12) NOT NULL DEFAULT 'CNY'",
            "created_time": "TEXT NOT NULL DEFAULT ''",
            "updated_time": "TEXT NOT NULL DEFAULT ''",
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
        ledger_columns = {
            item["name"] for item in inspect(connection).get_columns("ledger_entry")
        }
        connection.execute(text(
            "UPDATE ledger_entry SET "
            "created_time = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE created_time IS NULL OR created_time = ''"
        ))
        connection.execute(text(
            "UPDATE ledger_entry SET "
            "updated_time = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE updated_time IS NULL OR updated_time = ''"
        ))
        old_projection_columns = {
            "economic_type",
            "cash_direction",
            "in_amount_value",
            "out_amount_value",
            "in_currency_code",
            "out_currency_code",
            "in_account_code",
            "out_account_code",
            "start_time",
        }
        if old_projection_columns <= ledger_columns:
            connection.execute(text(
                "UPDATE ledger_entry SET "
                "entry_type = CASE economic_type "
                "WHEN 'ACCOUNT_TRANSFER' THEN 1 WHEN 'CLAIM' THEN 2 ELSE 0 END, "
                "entry_direction = CASE "
                "WHEN cash_direction = 'IN' THEN 1 "
                "WHEN cash_direction = 'OUT' THEN 2 "
                "WHEN in_amount_value > 0 AND out_amount_value = 0 THEN 1 "
                "WHEN out_amount_value > 0 AND in_amount_value = 0 THEN 2 "
                "ELSE entry_direction END, "
                "amount_value = CASE "
                "WHEN amount_value > 0 THEN amount_value "
                "WHEN in_amount_value > 0 AND out_amount_value = 0 THEN in_amount_value "
                "WHEN out_amount_value > 0 AND in_amount_value = 0 THEN out_amount_value "
                "ELSE amount_value END, "
                "currency_code = CASE "
                "WHEN cash_direction = 'IN' THEN in_currency_code "
                "WHEN cash_direction = 'OUT' THEN out_currency_code "
                "WHEN in_amount_value > 0 AND out_amount_value = 0 THEN in_currency_code "
                "WHEN out_amount_value > 0 AND in_amount_value = 0 THEN out_currency_code "
                "ELSE currency_code END, "
                "account_code = CASE "
                "WHEN cash_direction = 'IN' THEN in_account_code "
                "WHEN cash_direction = 'OUT' THEN out_account_code "
                "WHEN in_amount_value > 0 AND out_amount_value = 0 THEN in_account_code "
                "WHEN out_amount_value > 0 AND in_amount_value = 0 THEN out_account_code "
                "ELSE account_code END, "
                "occurred_time = COALESCE(occurred_time, start_time)"
            ))
        elif "start_time" in ledger_columns:
            connection.execute(text(
                "UPDATE ledger_entry "
                "SET occurred_time = COALESCE(occurred_time, start_time)"
            ))
        target_ledger_columns = {
            "id",
            "entry_type",
            "entry_direction",
            "amount_value",
            "amount_scale",
            "currency_code",
            "account_code",
            "counterparty_account_ref",
            "occurred_time",
            "created_time",
            "updated_time",
        }
        if ledger_columns != target_ledger_columns:
            connection.execute(text("DROP TABLE IF EXISTS ledger_entry_contract"))
            connection.execute(text("""
                CREATE TABLE ledger_entry_contract (
                    id INTEGER PRIMARY KEY,
                    entry_type INTEGER NOT NULL CHECK (entry_type IN (0, 1, 2)),
                    entry_direction INTEGER NOT NULL CHECK (entry_direction IN (1, 2)),
                    amount_value INTEGER NOT NULL CHECK (amount_value > 0),
                    amount_scale INTEGER NOT NULL DEFAULT 2 CHECK (amount_scale BETWEEN 0 AND 8),
                    currency_code TEXT NOT NULL CHECK (currency_code <> ''),
                    account_code TEXT NOT NULL CHECK (account_code <> ''),
                    counterparty_account_ref TEXT NOT NULL DEFAULT '',
                    occurred_time TEXT NOT NULL CHECK (occurred_time <> ''),
                    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                )
            """))
            connection.execute(text("""
                INSERT INTO ledger_entry_contract (
                    id, entry_type, entry_direction, amount_value, amount_scale,
                    currency_code, account_code, counterparty_account_ref,
                    occurred_time, created_time, updated_time
                )
                SELECT
                    id, entry_type, entry_direction, amount_value, amount_scale,
                    currency_code, account_code, counterparty_account_ref,
                    occurred_time, created_time, updated_time
                FROM ledger_entry
                WHERE entry_type IN (0, 1, 2)
                  AND entry_direction IN (1, 2)
                  AND amount_value > 0
                  AND currency_code <> ''
                  AND account_code <> ''
                  AND occurred_time IS NOT NULL
                  AND occurred_time <> ''
            """))
            connection.execute(text("DROP TABLE ledger_entry"))
            connection.execute(text(
                "ALTER TABLE ledger_entry_contract RENAME TO ledger_entry"
            ))
        connection.execute(text("DROP TABLE IF EXISTS ledger_entry_source"))
        for statement in TARGET_SQLITE_INDEXES:
            connection.execute(text(statement))


def init_target_db(bind=None) -> None:
    ensure_target_schema(bind=bind)
