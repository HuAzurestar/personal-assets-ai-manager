from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, SmallInteger, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TargetTable:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.current_timestamp()
    )
    updated_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.current_timestamp()
    )


class ImportFile(TargetTable, Base):
    __tablename__ = "import_file"
    __table_args__ = (
        Index(
            "uq_import_file_source_sha256",
            "source_type",
            "sha256",
            unique=True,
            sqlite_where=text("sha256 <> ''"),
        ),
    )

    batch_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    institution_code: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    file_format: Mapped[str] = mapped_column(String(12), nullable=False, default="UNKNOWN")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    period_start: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    period_end: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skip_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")


class BillRaw(TargetTable, Base):
    __tablename__ = "bill_raw"
    __table_args__ = (
        UniqueConstraint("import_file_id", "source_row_number", name="uq_bill_raw_file_row"),
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


class BillFact(TargetTable, Base):
    __tablename__ = "bill_fact"
    __table_args__ = (UniqueConstraint("fact_key", name="uq_bill_fact_key"),)

    fact_key: Mapped[str] = mapped_column(String(160), nullable=False)
    occurred_time: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    cash_direction: Mapped[str] = mapped_column(String(8), nullable=False)
    amount_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_scale: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)
    currency_code: Mapped[str] = mapped_column(String(12), nullable=False, default="CNY")
    account_code: Mapped[str] = mapped_column(String(120), nullable=False, default="UNKNOWN")
    counterparty: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ReviewCase(TargetTable, Base):
    __tablename__ = "review_case"

    review_type: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    allocation_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PARTIAL")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class ReviewCaseBill(TargetTable, Base):
    __tablename__ = "review_case_bill"

    case_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bill_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    role: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    party: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    amount_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    amount_scale: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)
    currency_code: Mapped[str] = mapped_column(String(12), nullable=False, default="CNY")


class ReviewHistory(TargetTable, Base):
    __tablename__ = "review_history"
    __table_args__ = (
        UniqueConstraint("case_id", "version", name="uq_review_history_case_version"),
        Index(
            "uq_review_history_idempotency",
            "idempotency_key",
            unique=True,
            sqlite_where=text("idempotency_key <> ''"),
        ),
    )

    case_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    operation: Mapped[str] = mapped_column(String(20), nullable=False, default="CREATE")
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    request_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    before_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    after_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    reverses_history_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actor: Mapped[str] = mapped_column(String(120), nullable=False, default="local-user")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False, default="")


class LedgerEntry(TargetTable, Base):
    __tablename__ = "ledger_entry"
    __table_args__ = (Index("ix_ledger_entry_time_id", "start_time", "id"),)

    ledger_type: Mapped[str] = mapped_column(String(40), nullable=False, default="UNRESOLVED")
    allocation_status: Mapped[str] = mapped_column(String(20), nullable=False, default="DEFAULT")
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    in_amount_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    in_amount_scale: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)
    in_currency_code: Mapped[str] = mapped_column(String(12), nullable=False, default="CNY")
    out_amount_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    out_amount_scale: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)
    out_currency_code: Mapped[str] = mapped_column(String(12), nullable=False, default="CNY")
    in_account_code: Mapped[str] = mapped_column(String(120), nullable=False, default="UNKNOWN")
    out_account_code: Mapped[str] = mapped_column(String(120), nullable=False, default="UNKNOWN")
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class LedgerEntrySource(TargetTable, Base):
    __tablename__ = "ledger_entry_source"
    __table_args__ = (
        UniqueConstraint("source_kind", "source_id", name="uq_ledger_entry_source"),
    )

    ledger_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="BILL_FACT")
    source_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TargetTagView(TargetTable, Base):
    __tablename__ = "tag_view"
    __table_args__ = (UniqueConstraint("system_name", name="uq_target_tag_view_system_name"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    system_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")


class TargetTag(TargetTable, Base):
    __tablename__ = "tag"
    __table_args__ = (
        UniqueConstraint("view_id", "system_name", name="uq_target_tag_system_name"),
    )

    view_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    system_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")


class LedgerEntryTag(TargetTable, Base):
    __tablename__ = "ledger_entry_tag"
    __table_args__ = (
        UniqueConstraint("ledger_id", "tag_id", name="uq_ledger_entry_tag"),
    )

    ledger_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tag_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
