from __future__ import annotations


from sqlalchemy import Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase

from backend.entity.base import TargetTable


class ImportFile(TargetTable, TargetBase):
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
