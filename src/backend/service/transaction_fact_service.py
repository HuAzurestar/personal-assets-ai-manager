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
    TransactionFactListItem,
    TransactionFactPageRead,
    TransactionFactRead,
    TransactionFactReviewRead,
    TransactionFactSorter,
)


class TransactionFactService:
    """Read-only Transaction Fact PO and relationship inspection."""

    def __init__(self, db: Session):
        self.mapper = TransactionFactMapper(db)
        self.allocation_mapper = TargetEconomicMapper(db)

    def page(
        self,
        *,
        page: int,
        page_size: int,
        q: str,
        filter_value: TransactionFactFilter,
        sorter: TransactionFactSorter,
    ) -> TransactionFactPageRead:
        rows, total = self.mapper.page(
            page=page,
            page_size=page_size,
            q=q,
            filter_value=filter_value,
            sorter=sorter,
        )
        return TransactionFactPageRead(
            items=[TransactionFactListItem(**row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            q=q,
            filter=filter_value,
            sorter=sorter,
        )

    def detail(self, fact_id: int) -> TransactionFactDetailRead:
        fact = self.mapper.detail(fact_id)
        if fact is None:
            raise TargetFactError(404, f"transaction fact {fact_id} not found")
        allocation_rows = self.allocation_mapper.allocations_by_relation(
            fact_ids=[fact_id],
        )
        review_ids = sorted({row["review_id"] for row in allocation_rows})
        economic_ids = sorted({
            row["economic_id"] for row in allocation_rows if row["economic_id"] > 0
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
                TransactionFactLedgerRead(**{
                    name: value
                    for name, value in row.items()
                    if name not in {"entry_type", "entry_direction"}
                })
                for row in self.mapper.ledgers(economic_ids)
            ],
        )
