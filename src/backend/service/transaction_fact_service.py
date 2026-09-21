from __future__ import annotations

from sqlalchemy.orm import Session

from backend.error import TargetFactError
from backend.mapper.target_economic_mapper import TargetEconomicMapper
from backend.mapper.transaction_fact_mapper import TransactionFactMapper
from backend.schema.transaction_fact import (
    TransactionFactAllocationRead,
    TransactionFactDetailRead,
    TransactionFactFilter,
    TransactionFactImportEvidenceRead,
    TransactionFactLedgerRead,
    TransactionFactListBody,
    TransactionFactListItem,
    TransactionFactListRequest,
    TransactionFactRead,
    TransactionFactReviewRead,
    TransactionFactSorter,
    parse_transaction_fact_time,
)
from backend.schema.list_query import BetweenValue, iter_filter_fields


class TransactionFactService:
    """Read-only Transaction Fact PO and relationship inspection."""

    def __init__(self, db: Session):
        self.mapper = TransactionFactMapper(db)
        self.allocation_mapper = TargetEconomicMapper(db)

    def page(
        self,
        *,
        request: TransactionFactListRequest,
    ) -> TransactionFactListBody:
        filter_value = self._mapper_filter(request)
        sorter_expression = request.sorter[0] if request.sorter else None
        sorter = TransactionFactSorter(
            field=sorter_expression.key if sorter_expression else "occurred_time",
            order=sorter_expression.direction if sorter_expression else "desc",
        )
        rows, total = self.mapper.page(
            page=request.page_index,
            page_size=request.page_size,
            filter_value=filter_value,
            sorter=sorter,
        )
        return TransactionFactListBody(
            items=[TransactionFactListItem(**row) for row in rows],
            total=total,
            page_index=request.page_index,
            page_size=request.page_size,
        )

    @staticmethod
    def _mapper_filter(
        request: TransactionFactListRequest,
    ) -> TransactionFactFilter:
        values: dict[str, object] = {}
        for expression in iter_filter_fields(request.filter):
            if expression.key == "occurred_time":
                if expression.op == "between":
                    between = BetweenValue.model_validate(expression.val)
                    values["occurred_time_start"] = parse_transaction_fact_time(
                        between.start
                    )
                    values["occurred_time_end"] = parse_transaction_fact_time(
                        between.end
                    )
                elif expression.op == ">=":
                    values["occurred_time_start"] = parse_transaction_fact_time(
                        expression.val
                    )
                else:
                    values["occurred_time_end"] = parse_transaction_fact_time(
                        expression.val
                    )
            elif expression.key == "currency_code":
                values[expression.key] = str(expression.val).strip().upper()
            elif expression.key == "account_code":
                values[expression.key] = str(expression.val).strip()
            else:
                values[expression.key] = expression.val
        return TransactionFactFilter.model_validate(values)

    def detail(self, fact_id: int) -> TransactionFactDetailRead:
        fact = self.mapper.detail(fact_id)
        if fact is None:
            raise TargetFactError(404, f"transaction fact {fact_id} not found")
        allocation_rows = self.allocation_mapper.allocations_by_relation(
            fact_ids=[fact_id],
        )
        review_ids = sorted({row["review_case_id"] for row in allocation_rows})
        economic_ids = sorted({
            row["ledger_entry_id"]
            for row in allocation_rows
            if row["ledger_entry_id"] > 0
        })
        return TransactionFactDetailRead(
            transaction_fact=TransactionFactRead(**fact),
            import_evidence=[
                TransactionFactImportEvidenceRead(**row)
                for row in self.mapper.import_evidence(fact_id)
            ],
            allocations=[
                TransactionFactAllocationRead(**row) for row in allocation_rows
            ],
            reviews=[
                TransactionFactReviewRead(**row)
                for row in self.mapper.reviews(review_ids)
            ],
            ledgers=[
                TransactionFactLedgerRead(**row)
                for row in self.mapper.ledgers(economic_ids)
            ],
        )
