from __future__ import annotations

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable


IMPORT_SOURCE_UNKNOWN = 0
IMPORT_SOURCE_MANUAL = 1
IMPORT_SOURCE_ALIPAY = 101
IMPORT_SOURCE_WECHAT = 102
IMPORT_SOURCE_CCB_BANK = 201
IMPORT_SOURCE_ABC_BANK = 202
IMPORT_SOURCE_CMB_BANK = 203

IMPORT_FILE_FORMAT_UNKNOWN = 0
IMPORT_FILE_FORMAT_CSV = 1
IMPORT_FILE_FORMAT_XLS = 2
IMPORT_FILE_FORMAT_XLSX = 3
IMPORT_FILE_FORMAT_PDF = 4

IMPORT_FILE_STATUS_PENDING = 0
IMPORT_FILE_STATUS_IMPORTED = 1
IMPORT_FILE_STATUS_PARTIAL = 2
IMPORT_FILE_STATUS_FAILED = 3


class TransactionImportFile(TargetTable, TargetBase):
    __tablename__ = "transaction_import_file"
    __table_args__ = (
        UniqueConstraint("sha256", name="uq_transaction_import_file_sha256"),
    )

    batch_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    source_type: Mapped[int] = mapped_column(
        Integer, nullable=False, default=IMPORT_SOURCE_UNKNOWN
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_format: Mapped[int] = mapped_column(
        Integer, nullable=False, default=IMPORT_FILE_FORMAT_UNKNOWN
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    period_end: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skip_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[int] = mapped_column(
        Integer, nullable=False, default=IMPORT_FILE_STATUS_PENDING
    )
