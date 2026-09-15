from __future__ import annotations


from sqlalchemy import Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase

from backend.entity.base import TargetTable


class BillRaw(TargetTable, TargetBase):
    __tablename__ = "bill_raw"
    __table_args__ = (
        UniqueConstraint("import_file_id", "source_row_number", name="uq_bill_raw_file_row"),
        Index("ix_bill_raw_bill_id_id", "bill_id", "id"),
        Index(
            "ix_bill_raw_source_reference_bill_id",
            "source_reference",
            "bill_id",
            sqlite_where=text("source_reference <> ''"),
        ),
    )

    bill_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    import_file_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_reference: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    raw_payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    parse_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    issue_code: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    issue_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
