"""Fresh-database boundary for the target PAAM schema."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from backend.core.config import DATABASE_URL, ensure_data_dir


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
    "review_allocation",
    "review_revision",
    "ledger_entry",
    "tag_view",
    "tag",
    "ledger_entry_tag",
)

SQL_ASSET_DIR = Path(__file__).resolve().parents[2] / "asset" / "sql"
UTC_TIMESTAMP_COLUMNS = {
    table_name: ("created_time", "updated_time")
    for table_name in TARGET_TABLE_NAMES
}
UTC_TIMESTAMP_COLUMNS["transaction_fact"] += ("occurred_time",)
UTC_TIMESTAMP_COLUMNS["ledger_entry"] += ("occurred_time",)


def _normalize_legacy_millisecond_timestamps(driver) -> None:
    """Pad legacy `.sssZ` values so text comparison and lock tokens stay exact."""
    for table_name, columns in UTC_TIMESTAMP_COLUMNS.items():
        for column_name in columns:
            driver.execute(
                f'UPDATE "{table_name}" '
                f'SET "{column_name}" = substr("{column_name}", 1, 23) || \'000Z\' '
                f'WHERE length("{column_name}") = 24 '
                f'AND substr("{column_name}", 24, 1) = \'Z\''
            )


def ensure_target_schema(bind=None) -> None:
    """Create the reviewed SQLite schema from the authoritative SQL assets."""

    target_bind = engine if bind is None else bind
    if target_bind.dialect.name != "sqlite":
        raise RuntimeError("PAAM target schema currently supports SQLite only")
    connection = target_bind.raw_connection()
    try:
        driver = getattr(connection, "driver_connection", connection)
        driver.execute("PRAGMA encoding = 'UTF-8'")
        for table_name in TARGET_TABLE_NAMES:
            path = SQL_ASSET_DIR / f"{table_name}.sql"
            driver.executescript(path.read_text(encoding="utf-8"))
        _normalize_legacy_millisecond_timestamps(driver)
        encoding = driver.execute("PRAGMA encoding").fetchone()[0]
        if encoding.upper().replace("-", "") != "UTF8":
            raise RuntimeError(f"SQLite database encoding is {encoding}, expected UTF-8")
        driver.commit()
    finally:
        connection.close()


def init_target_db(bind=None) -> None:
    ensure_target_schema(bind=bind)
