from __future__ import annotations

from sqlalchemy.orm import Session

from backend.error import TargetIntakeError
from backend.mapper.import_file_mapper import ImportFileMapper
from backend.mapper.transaction_fact_mapper import TransactionFactMapper
from backend.schema.import_file import (
    ImportFileDetailRead,
    ImportFileFilter,
    ImportFilePageRead,
    ImportFileRead,
    ImportFileSorter,
    ImportFileSummaryRead,
    ImportFileTransactionFactPageRead,
)
from backend.schema.transaction_fact import (
    TransactionFactFilter,
    TransactionFactListItem,
    TransactionFactSorter,
)


class ImportFileService:
    """Read-only Import File PO and child Transaction Fact inspection."""

    def __init__(self, db: Session):
        self.mapper = ImportFileMapper(db)
        self.fact_mapper = TransactionFactMapper(db)

    def page(
        self,
        *,
        page: int,
        page_size: int,
        q: str,
        filter_value: ImportFileFilter,
        sorter: ImportFileSorter,
    ) -> ImportFilePageRead:
        rows, total = self.mapper.page(
            page=page,
            page_size=page_size,
            q=q,
            filter_value=filter_value,
            sorter=sorter,
        )
        return ImportFilePageRead(
            items=[ImportFileRead(**row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            q=q,
            filter=filter_value,
            sorter=sorter,
        )

    def summary(
        self,
        *,
        q: str,
        filter_value: ImportFileFilter,
    ) -> ImportFileSummaryRead:
        return ImportFileSummaryRead(
            **self.mapper.summary(q, filter_value),
            q=q,
            filter=filter_value,
        )

    def detail(self, import_file_id: int) -> ImportFileDetailRead:
        row = self.mapper.detail(import_file_id)
        if row is None:
            raise TargetIntakeError(404, f"import file {import_file_id} not found")
        return ImportFileDetailRead(import_file=ImportFileRead(**row))

    def transaction_fact_page(
        self,
        import_file_id: int,
        *,
        page: int,
        page_size: int,
        q: str,
        filter_value: TransactionFactFilter,
        sorter: TransactionFactSorter,
    ) -> ImportFileTransactionFactPageRead:
        if self.mapper.detail(import_file_id) is None:
            raise TargetIntakeError(404, f"import file {import_file_id} not found")
        rows, total = self.fact_mapper.page(
            page=page,
            page_size=page_size,
            q=q,
            filter_value=filter_value,
            sorter=sorter,
            import_file_id=import_file_id,
        )
        return ImportFileTransactionFactPageRead(
            items=[TransactionFactListItem(**row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            q=q,
            filter=filter_value,
            sorter=sorter,
        )
