"""Fresh-database boundary for the target PAAM schema."""

from __future__ import annotations

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
    "bill_raw",
    "transaction_fact",
    "review_case",
    "review_allocation",
    "review_revision",
    "ledger_entry",
    "tag_view",
    "tag",
    "ledger_entry_tag",
)


def ensure_target_schema(bind=None) -> None:
    """Create the current schema; existing databases are not upgraded."""

    target_bind = engine if bind is None else bind
    for table_name in TARGET_TABLE_NAMES:
        TargetBase.metadata.tables[table_name].create(
            bind=target_bind,
            checkfirst=True,
        )


def init_target_db(bind=None) -> None:
    ensure_target_schema(bind=bind)
