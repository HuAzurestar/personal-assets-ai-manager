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
    ImportFileRowListBody,
    ImportFileRowListRequest,
    ImportFileRowRead,
    ImportFileSorter,
    ImportFileSummaryRead,
    ImportFileTransactionFactListRead,
    parse_import_file_time,
)
from backend.schema.transaction_fact import TransactionFactListItem
from backend.service.fact_read_service import fact_po
from backend.service.flow_read_service import limited
from backend.mapper.bounded_query_mapper import query_budget
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.error import TargetEconomicError


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
        return ImportFileDetailRead(
            import_file=ImportFileRead(**row),
            relation_summary=self.mapper.relation_summary(import_file_id),
        )

    def transaction_facts(
        self,
        import_file_id: int,
    ) -> ImportFileTransactionFactListRead:
        with query_budget(self.mapper.db):
            relations = TrustedRelationMapper(self.mapper.db)
            relations.read_snapshot()
            relations.validate()
            if self.mapper.detail(import_file_id) is None:
                raise TargetIntakeError(404, "import file not found")
            from backend.mapper.import_source_mapper import ImportSourceMapper
            if ImportSourceMapper(self.mapper.db).broken_file_sources(import_file_id):
                raise TargetEconomicError(409,"source relation is damaged",code="RELATION_BROKEN")
            rows = self.fact_mapper.by_import_file(import_file_id)
            if len(rows) > 4000:
                raise TargetEconomicError(413,"use source row pages",code="DETAIL_LIMIT")
            return limited(ImportFileTransactionFactListRead(items=[TransactionFactListItem(**fact_po(row)) for row in rows],total=len(rows)))

    def rows(
        self,
        import_file_id: int,
        *,
        request: ImportFileRowListRequest,
    ) -> ImportFileRowListBody:
        if self.mapper.detail(import_file_id) is None:
            raise TargetIntakeError(404, f"import file {import_file_id} not found")
        row_status = next(
            (expression.val for expression in iter_filter_fields(request.filter)),
            None,
        )
        sorter = request.sorter[0] if request.sorter else None
        rows, total = self.mapper.row_page(
            import_file_id,
            page=request.page_index,
            page_size=request.page_size,
            row_status=row_status,
            order=sorter.direction if sorter else "asc",
        )
        return ImportFileRowListBody(
            items=[ImportFileRowRead(**row) for row in rows],
            total=total,
            page_index=request.page_index,
            page_size=request.page_size,
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
