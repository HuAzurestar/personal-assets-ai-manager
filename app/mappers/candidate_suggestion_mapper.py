from __future__ import annotations

import json

from sqlalchemy import and_, func, select, tuple_, update
from sqlalchemy.orm import Session, aliased

from app.database import Bill, ReviewCandidate
from app.schemas.review import (
    CandidateSuggestionBillVO,
    CandidateSuggestionWriteVO,
    PendingDuplicateSuggestionVO,
)


class CandidateSuggestionMapper:
    """Bounded evidence reads and bulk writes for candidate suggestions."""

    def __init__(self, db: Session):
        self.db = db

    def target_bills(self, bill_ids: list[int]) -> tuple[CandidateSuggestionBillVO, ...]:
        if not bill_ids:
            return ()
        rows = self.db.execute(
            select(
                Bill.id,
                Bill.occurred_at,
                Bill.merchant,
                Bill.amount,
                Bill.account_name,
            ).where(Bill.id.in_(bill_ids)).order_by(Bill.id)
        ).mappings().all()
        return tuple(CandidateSuggestionBillVO(**row) for row in rows)

    def nearby_bills(self, bill_ids: list[int]) -> tuple[CandidateSuggestionBillVO, ...]:
        if not bill_ids:
            return ()
        target = aliased(Bill)
        statement = (
            select(
                Bill.id,
                Bill.occurred_at,
                Bill.merchant,
                Bill.amount,
                Bill.account_name,
            )
            .join(target, and_(
                target.id.in_(bill_ids),
                Bill.id != target.id,
                Bill.occurred_at.between(
                    func.datetime(target.occurred_at, "-5 minutes"),
                    func.datetime(target.occurred_at, "+5 minutes"),
                ),
                func.abs(func.abs(Bill.amount) - func.abs(target.amount)) <= 0.01,
            ))
            .distinct()
            .order_by(Bill.id)
        )
        rows = self.db.execute(statement).mappings().all()
        return tuple(CandidateSuggestionBillVO(**row) for row in rows)

    def duplicate_bills(
        self,
        keys: list[tuple[str, float]],
    ) -> tuple[CandidateSuggestionBillVO, ...]:
        if not keys:
            return ()
        statement = select(
            Bill.id,
            Bill.occurred_at,
            Bill.merchant,
            Bill.amount,
            Bill.account_name,
        ).where(tuple_(Bill.merchant, Bill.amount).in_(keys)).order_by(
            Bill.merchant,
            Bill.amount,
            Bill.occurred_at,
            Bill.id,
        )
        rows = self.db.execute(statement).mappings().all()
        return tuple(CandidateSuggestionBillVO(**row) for row in rows)

    def pending_duplicates(self) -> tuple[PendingDuplicateSuggestionVO, ...]:
        rows = self.db.execute(
            select(
                ReviewCandidate.id,
                ReviewCandidate.bill_id,
                ReviewCandidate.related_bill_id,
                ReviewCandidate.member_bill_ids,
                ReviewCandidate.reason,
            ).where(
                ReviewCandidate.candidate_type == "duplicate",
                ReviewCandidate.status.in_(("pending", "legacy_duplicate_needs_review")),
            ).order_by(ReviewCandidate.id)
        ).mappings().all()
        return tuple(PendingDuplicateSuggestionVO(
            id=row["id"],
            bill_id=row["bill_id"],
            related_bill_id=row["related_bill_id"],
            member_bill_ids=self._member_ids(row),
            reason=row["reason"],
        ) for row in rows)

    def save(
        self,
        updates: list[PendingDuplicateSuggestionVO],
        writes: list[CandidateSuggestionWriteVO],
    ) -> None:
        if updates:
            self.db.execute(update(ReviewCandidate), [{
                "id": candidate.id,
                "bill_id": candidate.bill_id,
                "related_bill_id": candidate.related_bill_id,
                "member_bill_ids": json.dumps(candidate.member_bill_ids),
                "group_fingerprint": "duplicate:" + ":".join(
                    str(bill_id) for bill_id in candidate.member_bill_ids
                ),
                "reason": candidate.reason,
            } for candidate in updates])
        if writes:
            self.db.add_all([ReviewCandidate(
                candidate_type=candidate.candidate_type,
                bill_id=candidate.bill_id,
                related_bill_id=candidate.related_bill_id,
                member_bill_ids=candidate.member_bill_ids,
                group_fingerprint=candidate.group_fingerprint,
                confidence=candidate.confidence,
                reason=candidate.reason,
                status=candidate.status,
                created_at=candidate.created_at,
            ) for candidate in writes])

    @staticmethod
    def _member_ids(row) -> tuple[int, ...]:
        try:
            encoded = json.loads(row["member_bill_ids"] or "[]")
        except (json.JSONDecodeError, TypeError):
            encoded = []
        return tuple(dict.fromkeys([
            *encoded,
            row["bill_id"],
            row["related_bill_id"],
        ]))
