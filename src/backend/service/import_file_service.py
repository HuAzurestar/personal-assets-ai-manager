from __future__ import annotations

from sqlalchemy.orm import Session

from backend.error import TargetIntakeError
from backend.mapper.import_file_mapper import ImportFileMapper
from backend.mapper.transaction_fact_mapper import TransactionFactMapper
from backend.schema.list_query import BetweenValue, iter_filter_fields
from backend.schema.import_file import (
    ImportFileDetailRead,
    ImportFileFilter,
    ImportFileListBody,
    ImportFileListRequest,
    ImportFileRead,
    ImportFileSorter,
    ImportFileSummaryRead,
    ImportFileTransactionFactListRead,
    parse_import_file_time,
)
from backend.schema.transaction_fact import TransactionFactListItem


class ImportFileService:
    """Read-only Import File PO and child Transaction Fact inspection."""

    def __init__(self, db: Session):
        self.mapper = ImportFileMapper(db)
        self.fact_mapper = TransactionFactMapper(db)

    def page(
        self,
        *,
        request: ImportFileListRequest,
    ) -> ImportFileListBody:
        filter_value = self._mapper_filter(request)
        sorter_expression = request.sorter[0] if request.sorter else None
        sorter = ImportFileSorter(
            field=sorter_expression.key if sorter_expression else "id",
            order=sorter_expression.direction if sorter_expression else "desc",
        )
        rows, total = self.mapper.page(
            page=request.page_index,
            page_size=request.page_size,
            filter_value=filter_value,
            sorter=sorter,
        )
        return ImportFileListBody(
            items=[ImportFileRead(**row) for row in rows],
            total=total,
            page_index=request.page_index,
            page_size=request.page_size,
        )

    def summary(
        self,
        *,
        request: ImportFileListRequest,
    ) -> ImportFileSummaryRead:
        return ImportFileSummaryRead(**self.mapper.summary(self._mapper_filter(request)))

    def detail(self, import_file_id: int) -> ImportFileDetailRead:
        row = self.mapper.detail(import_file_id)
        if row is None:
            raise TargetIntakeError(404, f"import file {import_file_id} not found")
        return ImportFileDetailRead(import_file=ImportFileRead(**row))

    def transaction_facts(
        self,
        import_file_id: int,
    ) -> ImportFileTransactionFactListRead:
        if self.mapper.detail(import_file_id) is None:
            raise TargetIntakeError(404, f"import file {import_file_id} not found")
        rows = self.fact_mapper.by_import_file(import_file_id)
        return ImportFileTransactionFactListRead(
            items=[TransactionFactListItem(**row) for row in rows],
            total=len(rows),
        )

    @staticmethod
    def _mapper_filter(request: ImportFileListRequest) -> ImportFileFilter:
        values: dict[str, object] = {}
        for expression in iter_filter_fields(request.filter):
            if expression.key in {"created_time", "updated_time"}:
                if expression.op == "between":
                    between = BetweenValue.model_validate(expression.val)
                    values[f"{expression.key}_start"] = parse_import_file_time(
                        between.start,
                        expression.key,
                    )
                    values[f"{expression.key}_end"] = parse_import_file_time(
                        between.end,
                        expression.key,
                    )
                elif expression.op == ">=":
                    values[f"{expression.key}_start"] = parse_import_file_time(
                        expression.val,
                        expression.key,
                    )
                else:
                    values[f"{expression.key}_end"] = parse_import_file_time(
                        expression.val,
                        expression.key,
                    )
            else:
                values[expression.key] = expression.val
        return ImportFileFilter.model_validate(values)
