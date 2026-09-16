from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable


IMPORT_ROW_STATUS_UNKNOWN = 0
IMPORT_ROW_STATUS_ACCEPTED = 1
IMPORT_ROW_STATUS_SKIPPED = 2
IMPORT_ROW_STATUS_INVALID = 3


class TransactionImportRow(TargetTable, TargetBase):
    __tablename__ = "transaction_import_row"
    __table_args__ = (
        UniqueConstraint(
            "transaction_import_file_id",
            "source_row_number",
            name="uq_transaction_import_row_file_row",
        ),
        CheckConstraint(
            "transaction_fact_id >= 0",
            name="ck_transaction_import_row_fact_id",
        ),
        CheckConstraint(
            "transaction_import_file_id > 0",
            name="ck_transaction_import_row_file_id",
        ),
        CheckConstraint(
            "source_row_number > 0",
            name="ck_transaction_import_row_source_row_number",
        ),
        CheckConstraint(
            "row_status IN (0, 1, 2, 3)",
            name="ck_transaction_import_row_status",
        ),
        CheckConstraint(
            "raw_payload IS NULL OR json_valid(raw_payload)",
            name="ck_transaction_import_row_raw_payload_json",
        ),
        Index(
            "ix_transaction_import_row_fact_id",
            "transaction_fact_id",
            "id",
        ),
        Index(
            "ix_transaction_import_row_reference_fact",
            "source_reference",
            "transaction_fact_id",
            sqlite_where=text("source_reference <> ''"),
        ),
    )

    transaction_fact_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    transaction_import_file_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_reference: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    row_status: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=IMPORT_ROW_STATUS_UNKNOWN,
    )
    issue_code: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    issue_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
