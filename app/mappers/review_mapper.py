from __future__ import annotations

import json

from sqlalchemy import and_, case, func, select, text, tuple_, union_all, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.errors import ReviewCommandError
from app.database import (
    Bill,
    CandidateActionLog,
    RefundAllocation,
    RefundDesignation,
    ReviewCandidate,
    ReviewMatter,
    ReviewMatterRevision,
)
from app.mappers.ledger_mapper import LedgerMapper
from app.schemas.review import (
    DuplicateBillVO,
    CandidateActionWriteVO,
    ExistingActionVO,
    MergeCandidateVO,
    ReviewCommandBillVO,
    ReviewCommandCandidateVO,
    ReviewCandidateVO,
    ReviewPageQuery,
    ReviewPageVO,
)


class ReviewMapper:
    """Set-based SQL for review candidate list and consolidation use cases."""

    def __init__(self, db: Session):
        self.db = db

    def page(self, query: ReviewPageQuery) -> ReviewPageVO:
        filters = self._filters(query.status, query.candidate_type)
        total = self.db.scalar(select(func.count(ReviewCandidate.id)).where(*filters)) or 0
        rows = self._candidate_rows(
            filters,
            offset=(query.page - 1) * query.page_size,
            limit=query.page_size,
        )
        return ReviewPageVO(
            items=self._hydrate(rows),
            total=total,
            page=query.page,
            page_size=query.page_size,
        )

    def all(self) -> tuple[ReviewCandidateVO, ...]:
        return self._hydrate(self._candidate_rows(self._filters(None, None)))

    def by_ids(self, candidate_ids: list[int]) -> tuple[ReviewCandidateVO, ...]:
        unique_ids = list(dict.fromkeys(candidate_ids))
        if not unique_ids:
            return ()
        rows = self._candidate_rows([ReviewCandidate.id.in_(unique_ids)])
        candidates = {candidate.id: candidate for candidate in self._hydrate(rows)}
        return tuple(candidates[candidate_id] for candidate_id in unique_ids if candidate_id in candidates)

    @staticmethod
    def _filters(status: str | None, candidate_type: str | None) -> list:
        filters = [ReviewCandidate.status != "superseded_duplicate_group"]
        if status == "transfer_grouped":
            filters.append(ReviewCandidate.status.in_((
                "transfer_grouped",
                "personal_transfer_grouped",
                "third_party_transfer_grouped",
            )))
        elif status == "needs_review":
            filters.append(ReviewCandidate.status.in_((
                "pending",
                "evidence_insufficient",
                "legacy_duplicate_needs_review",
            )))
        elif status:
            filters.append(ReviewCandidate.status == status)
        if candidate_type:
            filters.append(ReviewCandidate.candidate_type == candidate_type)
        return filters

    def _candidate_rows(self, filters: list, *, offset: int | None = None, limit: int | None = None):
        statement = select(
            ReviewCandidate.id,
            ReviewCandidate.candidate_type,
            ReviewCandidate.bill_id,
            ReviewCandidate.related_bill_id,
            ReviewCandidate.member_bill_ids,
            ReviewCandidate.confidence,
            ReviewCandidate.reason,
            ReviewCandidate.status,
            ReviewCandidate.transfer_group_id,
            ReviewCandidate.transfer_kind,
            ReviewCandidate.retained_bill_id,
            ReviewCandidate.resolved_at,
            ReviewCandidate.created_at,
        ).where(*filters).order_by(ReviewCandidate.created_at.desc(), ReviewCandidate.id.desc())
        if offset is not None:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        return self.db.execute(statement).mappings().all()

    def _hydrate(self, rows) -> tuple[ReviewCandidateVO, ...]:
        if not rows:
            return ()
        member_ids_by_candidate = {
            row["id"]: self._member_ids(row)
            for row in rows
        }
        bill_ids = list(dict.fromkeys(
            bill_id
            for member_ids in member_ids_by_candidate.values()
            for bill_id in member_ids
        ))
        bills = LedgerMapper(self.db).by_ids(bill_ids)
        actions = self._action_states([row["id"] for row in rows])
        hydrated = []
        for row in rows:
            member_ids = member_ids_by_candidate[row["id"]]
            members = tuple(sorted(
                (bills[bill_id] for bill_id in member_ids if bill_id in bills),
                key=lambda bill: (bill.occurred_at, bill.id),
            ))
            bill = bills.get(row["bill_id"])
            related_bill = bills.get(row["related_bill_id"])
            if not bill or not related_bill:
                raise RuntimeError(f"Candidate {row['id']} references a missing bill")
            current_action_id, undo_available = actions.get(row["id"], (0, False))
            hydrated.append(ReviewCandidateVO(
                id=row["id"],
                candidate_type=row["candidate_type"],
                confidence=row["confidence"],
                reason=row["reason"],
                status=row["status"],
                transfer_group_id=row["transfer_group_id"],
                transfer_kind=row["transfer_kind"],
                retained_bill_id=row["retained_bill_id"],
                resolved_at=row["resolved_at"],
                created_at=row["created_at"],
                current_action_id=current_action_id,
                undo_available=undo_available,
                member_bills=members,
                bill=bill,
                related_bill=related_bill,
            ))
        return tuple(hydrated)

    @staticmethod
    def _member_ids(row) -> tuple[int, ...]:
        try:
            stored = json.loads(row["member_bill_ids"] or "[]")
        except (json.JSONDecodeError, TypeError):
            stored = []
        if not isinstance(stored, list):
            stored = []
        return tuple(dict.fromkeys([
            *(int(value) for value in stored),
            row["bill_id"],
            row["related_bill_id"],
        ]))

    def _action_states(self, candidate_ids: list[int]) -> dict[int, tuple[int, bool]]:
        active_action_id = func.max(case(
            (
                and_(
                    CandidateActionLog.action != "undo",
                    CandidateActionLog.undone.is_(False),
                ),
                CandidateActionLog.id,
            ),
            else_=None,
        ))
        rows = self.db.execute(
            select(
                CandidateActionLog.candidate_id,
                func.max(CandidateActionLog.id).label("current_action_id"),
                active_action_id.label("active_action_id"),
            )
            .where(CandidateActionLog.candidate_id.in_(candidate_ids))
            .group_by(CandidateActionLog.candidate_id)
        ).mappings().all()
        return {
            row["candidate_id"]: (row["current_action_id"] or 0, bool(row["active_action_id"]))
            for row in rows
        }

    def mergeable_duplicate_candidates(self) -> tuple[MergeCandidateVO, ...]:
        rows = self.db.execute(
            select(
                ReviewCandidate.id,
                ReviewCandidate.bill_id,
                ReviewCandidate.related_bill_id,
                ReviewCandidate.member_bill_ids,
                ReviewCandidate.group_fingerprint,
            )
            .where(
                ReviewCandidate.candidate_type == "duplicate",
                ReviewCandidate.status.in_(("pending", "legacy_duplicate_needs_review")),
                ~ReviewCandidate.id.in_(select(CandidateActionLog.candidate_id)),
            )
            .order_by(ReviewCandidate.id)
        ).mappings().all()
        return tuple(MergeCandidateVO(
            id=row["id"],
            bill_id=row["bill_id"],
            related_bill_id=row["related_bill_id"],
            member_bill_ids=self._member_ids(row),
            group_fingerprint=row["group_fingerprint"],
        ) for row in rows)

    def duplicate_bills(self, seed_ids: list[int]) -> tuple[DuplicateBillVO, ...]:
        if not seed_ids:
            return ()
        seed_rows = self.db.execute(
            select(Bill.merchant, Bill.amount)
            .where(Bill.id.in_(seed_ids))
            .distinct()
        ).all()
        if not seed_rows:
            return ()
        rows = self.db.execute(
            select(Bill.id, Bill.occurred_at, Bill.merchant, Bill.amount)
            .where(tuple_(Bill.merchant, Bill.amount).in_([tuple(row) for row in seed_rows]))
            .order_by(Bill.merchant, Bill.amount, Bill.occurred_at, Bill.id)
        ).mappings().all()
        return tuple(DuplicateBillVO(
            id=row["id"],
            occurred_at=row["occurred_at"],
            merchant=row["merchant"],
            amount=row["amount"],
        ) for row in rows)

    def update_duplicate_groups(self, canonical: list[dict], superseded: list[dict]) -> None:
        if canonical:
            self.db.execute(update(ReviewCandidate), canonical)
        if superseded:
            self.db.execute(update(ReviewCandidate), superseded)

    def begin_immediate(self) -> None:
        try:
            self.db.execute(text("BEGIN IMMEDIATE"))
        except OperationalError as error:
            self.db.rollback()
            raise ReviewCommandError(409, "Ledger is busy; retry the review operation") from error

    def command_candidates(self, candidate_ids: list[int]) -> dict[int, ReviewCommandCandidateVO]:
        rows = self.db.execute(
            select(
                ReviewCandidate.id,
                ReviewCandidate.candidate_type,
                ReviewCandidate.bill_id,
                ReviewCandidate.related_bill_id,
                ReviewCandidate.member_bill_ids,
                ReviewCandidate.reason,
                ReviewCandidate.status,
                ReviewCandidate.transfer_group_id,
                ReviewCandidate.transfer_kind,
                ReviewCandidate.retained_bill_id,
                ReviewCandidate.resolved_at,
            ).where(ReviewCandidate.id.in_(candidate_ids))
        ).mappings().all()
        return {row["id"]: ReviewCommandCandidateVO(
            id=row["id"],
            candidate_type=row["candidate_type"],
            bill_id=row["bill_id"],
            related_bill_id=row["related_bill_id"],
            member_bill_ids=self._member_ids(row),
            reason=row["reason"],
            status=row["status"],
            transfer_group_id=row["transfer_group_id"],
            transfer_kind=row["transfer_kind"],
            retained_bill_id=row["retained_bill_id"],
            resolved_at=row["resolved_at"],
        ) for row in rows}

    def command_bills(self, bill_ids: list[int]) -> dict[int, ReviewCommandBillVO]:
        if not bill_ids:
            return {}
        rows = self.db.execute(
            select(
                Bill.id,
                Bill.occurred_at,
                Bill.amount,
                Bill.account_name,
                Bill.aggregate_excluded,
                Bill.transfer_group_id,
                Bill.duplicate_of_id,
            ).where(Bill.id.in_(bill_ids))
        ).mappings().all()
        return {row["id"]: ReviewCommandBillVO(
            id=row["id"],
            occurred_at=row["occurred_at"],
            amount=row["amount"],
            account_name=row["account_name"],
            aggregate_excluded=row["aggregate_excluded"],
            transfer_group_id=row["transfer_group_id"],
            duplicate_of_id=row["duplicate_of_id"],
        ) for row in rows}

    def latest_action_ids(self, candidate_ids: list[int]) -> dict[int, int]:
        rows = self.db.execute(
            select(
                CandidateActionLog.candidate_id,
                func.max(CandidateActionLog.id).label("action_id"),
            )
            .where(CandidateActionLog.candidate_id.in_(candidate_ids))
            .group_by(CandidateActionLog.candidate_id)
        ).mappings().all()
        return {row["candidate_id"]: row["action_id"] for row in rows}

    def existing_actions(self, idempotency_keys: list[str]) -> dict[str, ExistingActionVO]:
        if not idempotency_keys:
            return {}
        rows = self.db.execute(
            select(
                CandidateActionLog.id,
                CandidateActionLog.candidate_id,
                CandidateActionLog.idempotency_key,
                CandidateActionLog.request_payload,
                CandidateActionLog.undone,
            ).where(CandidateActionLog.idempotency_key.in_(idempotency_keys))
        ).mappings().all()
        return {row["idempotency_key"]: ExistingActionVO(
            id=row["id"],
            candidate_id=row["candidate_id"],
            idempotency_key=row["idempotency_key"],
            request_payload=row["request_payload"],
            undone=row["undone"],
        ) for row in rows}

    def active_candidate_owners(self) -> dict[int, list[int]]:
        rows = self.db.execute(
            select(
                ReviewCandidate.id,
                ReviewCandidate.bill_id,
                ReviewCandidate.related_bill_id,
                ReviewCandidate.member_bill_ids,
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
        owners: dict[int, list[int]] = {}
        for row in rows:
            for bill_id in self._member_ids(row):
                owners.setdefault(bill_id, []).append(row["id"])
        return owners

    def current_matter_owners(self) -> dict[int, list[int]]:
        rows = self.db.execute(
            select(
                ReviewMatterRevision.matter_id,
                ReviewMatterRevision.action,
                ReviewMatterRevision.snapshot,
            )
            .join(ReviewMatter, and_(
                ReviewMatter.id == ReviewMatterRevision.matter_id,
                ReviewMatter.version == ReviewMatterRevision.version,
            ))
            .order_by(ReviewMatterRevision.matter_id)
        ).mappings().all()
        owners: dict[int, list[int]] = {}
        for row in rows:
            if row["action"] == "revoke":
                continue
            snapshot = json.loads(row["snapshot"])
            for line in snapshot["lines"]:
                owners.setdefault(int(line["bill_id"]), []).append(row["matter_id"])
        return owners

    def refund_bill_ids(self, bill_ids: list[int]) -> set[int]:
        if not bill_ids:
            return set()
        occupied = union_all(
            select(RefundDesignation.bill_id.label("bill_id"))
            .where(RefundDesignation.bill_id.in_(bill_ids)),
            select(RefundAllocation.refund_bill_id.label("bill_id"))
            .where(
                RefundAllocation.status == "confirmed",
                RefundAllocation.refund_bill_id.in_(bill_ids),
            ),
            select(RefundAllocation.expense_bill_id.label("bill_id"))
            .where(
                RefundAllocation.status == "confirmed",
                RefundAllocation.expense_bill_id.in_(bill_ids),
            ),
        ).subquery()
        return set(self.db.scalars(select(occupied.c.bill_id).distinct()).all())

    def save_batch(
        self,
        candidate_updates: list[dict],
        bill_updates: list[dict],
        actions: list[CandidateActionWriteVO],
    ) -> None:
        if candidate_updates:
            self.db.execute(update(ReviewCandidate), candidate_updates)
        if bill_updates:
            self.db.execute(update(Bill), bill_updates)
        if actions:
            self.db.add_all([CandidateActionLog(
                candidate_id=action.candidate_id,
                action=action.action,
                before_state=action.before_state,
                after_state=action.after_state,
                actor=action.actor,
                reason=action.reason,
                idempotency_key=action.idempotency_key,
                request_payload=action.request_payload,
                created_at=action.created_at,
            ) for action in actions])
