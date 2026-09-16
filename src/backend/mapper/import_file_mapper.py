from __future__ import annotations

from sqlalchemy import String, case, cast, func, or_, select
from sqlalchemy.orm import Session

from backend.entity import ImportFile
from backend.schema.import_file import ImportFileFilter, ImportFileSorter


class ImportFileMapper:
    """Read-only PO queries for Import File detail surfaces."""

    _SORT_COLUMNS = {
        "id": ImportFile.id,
        "filename": ImportFile.filename,
        "created_time": ImportFile.created_time,
        "updated_time": ImportFile.updated_time,
    }

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _clauses(q: str, filter_value: ImportFileFilter):
        clauses = []
        if q:
            pattern = f"%{q}%"
            clauses.append(or_(
                cast(ImportFile.id, String).like(pattern),
                ImportFile.batch_code.like(pattern),
                ImportFile.filename.like(pattern),
                ImportFile.source_type.like(pattern),
                ImportFile.institution_code.like(pattern),
            ))
        for field in ("source_type", "institution_code", "file_format", "status"):
            value = getattr(filter_value, field)
            if value:
                clauses.append(getattr(ImportFile, field) == value)
        return clauses

    @staticmethod
    def _columns():
        return (
            ImportFile.id,
            ImportFile.batch_code,
            ImportFile.source_type,
            ImportFile.institution_code,
            ImportFile.filename,
            ImportFile.file_format,
            ImportFile.sha256,
            ImportFile.period_start,
            ImportFile.period_end,
            ImportFile.total_count,
            ImportFile.success_count,
            ImportFile.skip_count,
            ImportFile.issue_count,
            ImportFile.status,
            ImportFile.created_time,
            ImportFile.updated_time,
        )

    def page(
        self,
        *,
        page: int,
        page_size: int,
        q: str,
        filter_value: ImportFileFilter,
        sorter: ImportFileSorter,
    ) -> tuple[list[dict], int]:
        clauses = self._clauses(q, filter_value)
        total = int(self.db.scalar(
            select(func.count(ImportFile.id)).where(*clauses)
        ) or 0)
        column = self._SORT_COLUMNS[sorter.field]
        order = column.asc() if sorter.order == "asc" else column.desc()
        id_order = ImportFile.id.asc() if sorter.order == "asc" else ImportFile.id.desc()
        rows = self.db.execute(select(
            *self._columns(),
        ).where(*clauses).order_by(order, id_order).offset(
            (page - 1) * page_size
        ).limit(page_size)).mappings().all()
        return [dict(row) for row in rows], total

    def summary(self, q: str, filter_value: ImportFileFilter) -> dict:
        row = self.db.execute(select(
            func.count(ImportFile.id).label("import_file_count"),
            func.coalesce(func.sum(case(
                (ImportFile.status == "IMPORTED", 1),
                else_=0,
            )), 0).label("imported_file_count"),
            func.coalesce(func.sum(ImportFile.total_count), 0).label("row_count"),
            func.coalesce(func.sum(ImportFile.success_count), 0).label("success_count"),
            func.coalesce(func.sum(ImportFile.skip_count), 0).label("skip_count"),
            func.coalesce(func.sum(ImportFile.issue_count), 0).label("issue_count"),
        ).where(*self._clauses(q, filter_value))).mappings().one()
        return {key: int(value) for key, value in row.items()}

    def detail(self, import_file_id: int) -> dict | None:
        row = self.db.execute(select(
            *self._columns(),
        ).where(ImportFile.id == import_file_id)).mappings().one_or_none()
        return dict(row) if row is not None else None
