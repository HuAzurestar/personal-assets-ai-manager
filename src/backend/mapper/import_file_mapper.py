from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from backend.entity import (
    IMPORT_FILE_FORMAT_CSV,
    IMPORT_FILE_FORMAT_PDF,
    IMPORT_FILE_FORMAT_UNKNOWN,
    IMPORT_FILE_FORMAT_XLS,
    IMPORT_FILE_FORMAT_XLSX,
    IMPORT_FILE_STATUS_FAILED,
    IMPORT_FILE_STATUS_IMPORTED,
    IMPORT_FILE_STATUS_PARTIAL,
    IMPORT_FILE_STATUS_PENDING,
    IMPORT_SOURCE_ABC_BANK,
    IMPORT_SOURCE_ALIPAY,
    IMPORT_SOURCE_CCB_BANK,
    IMPORT_SOURCE_CMB_BANK,
    IMPORT_SOURCE_MANUAL,
    IMPORT_SOURCE_UNKNOWN,
    IMPORT_SOURCE_WECHAT,
    TransactionImportFile,
)
from backend.schema.import_file import ImportFileFilter, ImportFileSorter


class ImportFileMapper:
    """Read-only PO queries for Import File detail surfaces."""

    _SORT_COLUMNS = {
        "id": TransactionImportFile.id,
        "filename": TransactionImportFile.filename,
        "created_time": TransactionImportFile.created_time,
        "updated_time": TransactionImportFile.updated_time,
    }

    _SOURCE_CODES = {
        "unknown": IMPORT_SOURCE_UNKNOWN,
        "manual": IMPORT_SOURCE_MANUAL,
        "alipay": IMPORT_SOURCE_ALIPAY,
        "wechat": IMPORT_SOURCE_WECHAT,
        "ccb": IMPORT_SOURCE_CCB_BANK,
        "abc": IMPORT_SOURCE_ABC_BANK,
        "cmb": IMPORT_SOURCE_CMB_BANK,
    }
    _FORMAT_CODES = {
        "UNKNOWN": IMPORT_FILE_FORMAT_UNKNOWN,
        "CSV": IMPORT_FILE_FORMAT_CSV,
        "XLS": IMPORT_FILE_FORMAT_XLS,
        "XLSX": IMPORT_FILE_FORMAT_XLSX,
        "PDF": IMPORT_FILE_FORMAT_PDF,
    }
    _STATUS_CODES = {
        "PENDING": IMPORT_FILE_STATUS_PENDING,
        "IMPORTED": IMPORT_FILE_STATUS_IMPORTED,
        "PARTIAL": IMPORT_FILE_STATUS_PARTIAL,
        "FAILED": IMPORT_FILE_STATUS_FAILED,
    }

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _clauses(filter_value: ImportFileFilter):
        clauses = []
        if filter_value.id:
            clauses.append(TransactionImportFile.id == filter_value.id)
        if filter_value.source_type is not None:
            clauses.append(TransactionImportFile.source_type == filter_value.source_type)
        if filter_value.file_format is not None:
            clauses.append(TransactionImportFile.file_format == filter_value.file_format)
        if filter_value.status is not None:
            clauses.append(TransactionImportFile.status == filter_value.status)
        if filter_value.created_time_start:
            clauses.append(TransactionImportFile.created_time >= filter_value.created_time_start)
        if filter_value.created_time_end:
            clauses.append(TransactionImportFile.created_time < filter_value.created_time_end)
        if filter_value.updated_time_start:
            clauses.append(TransactionImportFile.updated_time >= filter_value.updated_time_start)
        if filter_value.updated_time_end:
            clauses.append(TransactionImportFile.updated_time < filter_value.updated_time_end)
        return clauses

    @staticmethod
    def _columns():
        return (
            TransactionImportFile.id,
            TransactionImportFile.batch_code,
            TransactionImportFile.source_type,
            TransactionImportFile.filename,
            TransactionImportFile.file_format,
            TransactionImportFile.sha256,
            TransactionImportFile.period_start,
            TransactionImportFile.period_end,
            TransactionImportFile.total_count,
            TransactionImportFile.success_count,
            TransactionImportFile.skip_count,
            TransactionImportFile.issue_count,
            TransactionImportFile.status,
            TransactionImportFile.created_time,
            TransactionImportFile.updated_time,
        )

    def page(
        self,
        *,
        page: int,
        page_size: int,
        filter_value: ImportFileFilter,
        sorter: ImportFileSorter,
    ) -> tuple[list[dict], int]:
        clauses = self._clauses(filter_value)
        total = int(self.db.scalar(
            select(func.count(TransactionImportFile.id)).where(*clauses)
        ) or 0)
        column = self._SORT_COLUMNS[sorter.field]
        order = column.asc() if sorter.order == "asc" else column.desc()
        id_order = TransactionImportFile.id.asc() if sorter.order == "asc" else TransactionImportFile.id.desc()
        rows = self.db.execute(select(
            *self._columns(),
        ).where(*clauses).order_by(order, id_order).offset(
            (page - 1) * page_size
        ).limit(page_size)).mappings().all()
        return [dict(row) for row in rows], total

    def summary(self, filter_value: ImportFileFilter) -> dict:
        row = self.db.execute(select(
            func.count(TransactionImportFile.id).label("import_file_count"),
            func.coalesce(func.sum(case(
                (TransactionImportFile.status == IMPORT_FILE_STATUS_IMPORTED, 1),
                else_=0,
            )), 0).label("imported_file_count"),
            func.coalesce(func.sum(TransactionImportFile.total_count), 0).label("row_count"),
            func.coalesce(func.sum(TransactionImportFile.success_count), 0).label("success_count"),
            func.coalesce(func.sum(TransactionImportFile.skip_count), 0).label("skip_count"),
            func.coalesce(func.sum(TransactionImportFile.issue_count), 0).label("issue_count"),
        ).where(*self._clauses(filter_value))).mappings().one()
        return {key: int(value) for key, value in row.items()}

    def detail(self, import_file_id: int) -> dict | None:
        row = self.db.execute(select(
            *self._columns(),
        ).where(TransactionImportFile.id == import_file_id)).mappings().one_or_none()
        return dict(row) if row is not None else None
