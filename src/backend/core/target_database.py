"""Fresh-database boundary for the target PAAM schema."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from backend.core.config import DATABASE_URL, ensure_data_dir
from backend.core.stored_timestamp import check_timestamps


ensure_data_dir()
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
    if DATABASE_URL.startswith("sqlite")
    else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class TargetBase(DeclarativeBase):
    pass


from backend import entity as _target_models  # noqa: E402,F401


TARGET_TABLE_NAMES = (
    "transaction_import_file",
    "transaction_import_row",
    "transaction_fact",
    "review_case",
    "review_transaction_ledger_allocation",
    "review_revision",
    "ledger_entry",
    "tag_view",
    "tag",
    "ledger_entry_tag",
    "setting",
    "auto_tag_rule",
    "tag_assignment_request",
    "llm_prompt_audit",
    "ledger_account_party",
    "ledger_account",
    "ledger_account_ref",
    "position",
    "position_leg",
    "review_ledger_position_leg_allocation",
)

SQL_ASSET_DIR = Path(__file__).resolve().parents[2] / "asset" / "sql"
UTC_TIMESTAMP_COLUMNS = {
    table_name: ("created_time", "updated_time")
    for table_name in TARGET_TABLE_NAMES
    # This table is new and append-heavy; it has no legacy millisecond rows.
    if table_name != "llm_prompt_audit"
}
UTC_TIMESTAMP_COLUMNS["transaction_fact"] += ("occurred_time",)
UTC_TIMESTAMP_COLUMNS["ledger_entry"] += ("occurred_time",)
UTC_TIMESTAMP_COLUMNS["position_leg"] += ("occurred_time",)


def ensure_target_schema(bind=None) -> None:
    """Create the reviewed SQLite schema from the authoritative SQL assets."""

    target_bind = engine if bind is None else bind
    if target_bind.dialect.name != "sqlite":
        raise RuntimeError("PAAM target schema currently supports SQLite only")
    connection = target_bind.raw_connection()
    try:
        driver = getattr(connection, "driver_connection", connection)
        if driver.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='review_allocation'").fetchone():
            raise RuntimeError("SCHEMA_MIGRATION_REQUIRED: run the isolated PIRC-35 copy migration before opening this database")
        driver.execute("PRAGMA encoding = 'UTF-8'")
        for table_name in TARGET_TABLE_NAMES:
            path = SQL_ASSET_DIR / f"{table_name}.sql"
            driver.executescript(path.read_text(encoding="utf-8"))
        # Opening a target database cannot repair its immutable business times.
        # The offline copy migration registers and proves equivalent padding.
        check_timestamps(driver,TARGET_TABLE_NAMES)
        encoding = driver.execute("PRAGMA encoding").fetchone()[0]
        if encoding.upper().replace("-", "") != "UTF8":
            raise RuntimeError(f"SQLite database encoding is {encoding}, expected UTF-8")
        driver.commit()
    finally:
        connection.close()


def init_target_db(bind=None) -> None:
    ensure_target_schema(bind=bind)
