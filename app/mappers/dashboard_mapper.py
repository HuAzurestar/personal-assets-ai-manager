from __future__ import annotations

import json
from dataclasses import replace

from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from app.database import (
    Bill,
    ImportRowIssue,
    LedgerOrigin,
    RefundAllocation,
    RefundDesignation,
    ReviewCandidate,
    ReviewMatter,
    ReviewMatterRevision,
)
from app.mappers.ledger_mapper import LedgerMapper
from app.schemas.dashboard import (
    DashboardAllocationVO,
    DashboardBillVO,
    DashboardCandidateVO,
    DashboardDataVO,
    DashboardMatterVO,
)
from app.schemas.ledger import LedgerPageQuery


class DashboardMapper:
    """Load the summary read model using explicit columns and set queries."""

    def __init__(self, db: Session):
        self.db = db
        self.ledger = LedgerMapper(db)

    def load(self, query: LedgerPageQuery) -> DashboardDataVO:
        tag_dictionary = self.ledger.tag_dictionary() if query.tag else ((), {}, {})
        clauses = self.ledger.filter_clauses(query, tag_dictionary=tag_dictionary)
        bill_rows = self.db.execute(
            select(Bill.id, Bill.occurred_at, Bill.merchant, Bill.amount)
            .where(*clauses)
            .order_by(Bill.occurred_at, Bill.id)
        ).mappings().all()
        bills = tuple(DashboardBillVO(
            id=row["id"],
            occurred_at=row["occurred_at"],
            merchant=row["merchant"],
            amount=row["amount"],
        ) for row in bill_rows)
        bill_ids = [bill.id for bill in bills]

        allocation_rows = self.db.execute(
            select(
                RefundAllocation.id,
                RefundAllocation.refund_bill_id,
                RefundAllocation.expense_bill_id,
                RefundAllocation.amount,
            ).where(
                RefundAllocation.status == "confirmed",
                RefundAllocation.refund_bill_id.in_(bill_ids),
                RefundAllocation.expense_bill_id.in_(
                    select(Bill.id).where(Bill.aggregate_excluded.is_(False))
                ),
            )
        ).mappings().all() if bill_ids else []
        allocations = tuple(DashboardAllocationVO(
            id=row["id"],
            refund_bill_id=row["refund_bill_id"],
            expense_bill_id=row["expense_bill_id"],
            amount=row["amount"],
        ) for row in allocation_rows)

        refund_bill_ids = frozenset(self.db.scalars(
            select(RefundDesignation.bill_id).where(RefundDesignation.bill_id.in_(bill_ids))
        ).all()) if bill_ids else frozenset()
        matters = self._current_matters()
        warning_candidates = self._warning_candidates()

        cash_query = replace(query, scope="all")
        cash_clauses = self.ledger.filter_clauses(cash_query, tag_dictionary=tag_dictionary)
        cash_amounts = tuple(self.db.scalars(
            select(Bill.amount).where(*cash_clauses, Bill.duplicate_of_id.is_(None))
        ).all())

        import_count_expr = (
            select(func.count(LedgerOrigin.id))
            .where(LedgerOrigin.bill_id.in_(bill_ids))
            .scalar_subquery()
            if bill_ids else literal(0)
        )
        counts = self.db.execute(select(
            select(func.count(ImportRowIssue.id))
            .where(ImportRowIssue.resolved_at.is_(None))
            .scalar_subquery()
            .label("issue_count"),
            import_count_expr.label("import_count"),
            select(func.count(ReviewCandidate.id))
            .where(ReviewCandidate.status == "pending")
            .scalar_subquery()
            .label("candidate_count"),
            select(func.count(func.distinct(Bill.transfer_group_id)))
            .where(Bill.transfer_group_id.is_not(None))
            .scalar_subquery()
            .label("transfer_group_count"),
        )).mappings().one()
        return DashboardDataVO(
            bills=bills,
            allocations=allocations,
            refund_bill_ids=refund_bill_ids,
            matters=matters,
            warning_candidates=warning_candidates,
            cash_amounts=cash_amounts,
            issue_count=counts["issue_count"],
            import_count=counts["import_count"],
            candidate_count=counts["candidate_count"],
            transfer_group_count=counts["transfer_group_count"],
        )

    def _current_matters(self) -> tuple[DashboardMatterVO, ...]:
        rows = self.db.execute(
            select(
                ReviewMatterRevision.matter_id,
                ReviewMatterRevision.action,
                ReviewMatterRevision.snapshot,
            )
            .join(ReviewMatter, (
                (ReviewMatter.id == ReviewMatterRevision.matter_id)
                & (ReviewMatter.version == ReviewMatterRevision.version)
            ))
            .order_by(ReviewMatterRevision.matter_id)
        ).mappings().all()
        matters = []
        for row in rows:
            if row["action"] == "revoke":
                continue
            snapshot = json.loads(row["snapshot"])
            matters.append(DashboardMatterVO(
                id=row["matter_id"],
                lines=tuple(snapshot["lines"]),
                balances=tuple(snapshot["balances"]),
            ))
        return tuple(matters)

    def _warning_candidates(self) -> tuple[DashboardCandidateVO, ...]:
        rows = self.db.execute(
            select(
                ReviewCandidate.id,
                ReviewCandidate.bill_id,
                ReviewCandidate.related_bill_id,
                ReviewCandidate.member_bill_ids,
                ReviewCandidate.status,
            )
            .where(ReviewCandidate.status.in_((
                "duplicate_excluded",
                "personal_transfer_grouped",
                "third_party_transfer_grouped",
                "transfer_grouped",
                "legacy_transfer_excluded",
            )))
            .order_by(ReviewCandidate.id)
        ).mappings().all()
        candidates = []
        for row in rows:
            try:
                stored = json.loads(row["member_bill_ids"] or "[]")
            except (json.JSONDecodeError, TypeError):
                stored = []
            if not isinstance(stored, list):
                stored = []
            member_ids = tuple(dict.fromkeys([
                *(int(value) for value in stored),
                row["bill_id"],
                row["related_bill_id"],
            ]))
            candidates.append(DashboardCandidateVO(
                id=row["id"],
                status=row["status"],
                member_bill_ids=member_ids,
            ))
        return tuple(candidates)
