from sqlalchemy.orm import Session

from app.mappers.ledger_mapper import LedgerMapper
from app.schemas import BillRead, TransactionPageRead, ViewTagAssignmentRead
from app.schemas.ledger import LedgerBillVO, LedgerPageQuery


class LedgerService:
    """Ledger list use cases; business orchestration lives here, never in DTOs."""

    def __init__(self, db: Session):
        self.mapper = LedgerMapper(db)

    def page(self, query: LedgerPageQuery) -> TransactionPageRead:
        page = self.mapper.page(query)
        items = [bill_read_from_vo(bill) for bill in page.items]
        return TransactionPageRead(
            items=items,
            total=page.total,
            page=page.page,
            page_size=page.page_size,
            filters=page.filters,
            sort=page.sort,
        )

    def all(self) -> list[BillRead]:
        """Serve the legacy unpaged endpoint without N+1 SQL."""
        return [bill_read_from_vo(bill) for bill in self.mapper.all()]


def bill_read_from_vo(bill: LedgerBillVO) -> BillRead:
    assignments = [ViewTagAssignmentRead(
        view_id=tag.view_id,
        view_name=tag.view_name,
        view_system_name=tag.view_system_name,
        tag_id=tag.tag_id,
        tag_name=tag.tag_name,
        tag_system_name=tag.tag_system_name,
        strategy="tag_state",
        confidence=0.95,
    ) for tag in bill.tags]
    return BillRead(
        id=bill.id,
        occurred_at=bill.occurred_at,
        merchant=bill.merchant,
        note=bill.note,
        amount=bill.amount,
        currency=bill.currency,
        category=bill.category,
        tags=[tag.tag_name for tag in bill.tags],
        source_type=bill.source_type,
        source_reference=bill.source_reference,
        import_batch_id=bill.import_batch_id,
        account_name=bill.account_name,
        direction="收入" if bill.amount >= 0 else "支出",
        aggregate_excluded=bill.aggregate_excluded,
        transfer_group_id=bill.transfer_group_id,
        duplicate_of_id=bill.duplicate_of_id,
        view_tags=assignments,
        tag_state=bill.tag_state,
        tag_revision_id=bill.tag_revision_id,
    )
