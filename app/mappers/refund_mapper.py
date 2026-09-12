from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.errors import RefundCommandError
from app.database import (
    Bill,
    RefundAllocation,
    RefundAllocationAudit,
    RefundDesignation,
    RefundNatureAudit,
)
from app.mappers.ledger_mapper import LedgerMapper
from app.schemas.ledger import LedgerBillVO
from app.schemas.refund import (
    RefundAllocationAuditVO,
    RefundAllocationCommandVO,
    RefundAllocationVO,
    RefundBillSummaryVO,
    RefundCommandBillVO,
    RefundNatureAuditVO,
    RefundNatureCommandAuditVO,
)


class RefundMapper:
    """Explicit-column, set-based SQL for refund reads and commands."""

    def __init__(self, db: Session):
        self.db = db

    def begin_immediate(self) -> None:
        try:
            self.db.execute(text("BEGIN IMMEDIATE"))
        except OperationalError as error:
            self.db.rollback()
            raise RefundCommandError(409, "Ledger is busy; retry the review operation") from error

    def designated_bill_ids(self) -> list[int]:
        return list(self.db.scalars(
            select(RefundDesignation.bill_id)
            .order_by(RefundDesignation.bill_id.desc())
        ).all())

    def designated_page(self, *, page: int, page_size: int) -> tuple[int, list[int]]:
        total = self.db.scalar(select(func.count(RefundDesignation.bill_id))) or 0
        bill_ids = list(self.db.scalars(
            select(RefundDesignation.bill_id)
            .order_by(RefundDesignation.bill_id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all())
        return total, bill_ids

    def is_designated(self, bill_id: int) -> bool:
        return self.db.scalar(
            select(RefundDesignation.bill_id)
            .where(RefundDesignation.bill_id == bill_id)
        ) is not None

    def bills(self, bill_ids: list[int]) -> dict[int, LedgerBillVO]:
        return LedgerMapper(self.db).by_ids(bill_ids)

    def summary_bills(self, bill_ids: list[int]) -> dict[int, RefundBillSummaryVO]:
        if not bill_ids:
            return {}
        rows = self.db.execute(
            select(
                Bill.id,
                Bill.occurred_at,
                Bill.merchant,
                Bill.amount,
                Bill.currency,
                Bill.account_name,
            ).where(Bill.id.in_(bill_ids))
        ).mappings().all()
        return {row["id"]: RefundBillSummaryVO(**row) for row in rows}

    def allocations(self, bill_ids: list[int]) -> tuple[RefundAllocationVO, ...]:
        if not bill_ids:
            return ()
        rows = self.db.execute(
            select(
                RefundAllocation.id,
                RefundAllocation.refund_bill_id,
                RefundAllocation.expense_bill_id,
                RefundAllocation.amount,
                RefundAllocation.status,
                RefundAllocation.idempotency_key,
                RefundAllocation.created_at,
                RefundAllocation.revoked_at,
            )
            .where(RefundAllocation.refund_bill_id.in_(bill_ids))
            .order_by(RefundAllocation.refund_bill_id, RefundAllocation.id)
        ).mappings().all()
        return tuple(RefundAllocationVO(**row) for row in rows)

    def nature_audits(self, bill_ids: list[int]) -> tuple[RefundNatureAuditVO, ...]:
        if not bill_ids:
            return ()
        rows = self.db.execute(
            select(
                RefundNatureAudit.id,
                RefundNatureAudit.bill_id,
                RefundNatureAudit.action,
                RefundNatureAudit.reason,
                RefundNatureAudit.actor,
                RefundNatureAudit.before_nature,
                RefundNatureAudit.after_nature,
                RefundNatureAudit.idempotency_key,
                RefundNatureAudit.created_at,
            )
            .where(RefundNatureAudit.bill_id.in_(bill_ids))
            .order_by(RefundNatureAudit.bill_id, RefundNatureAudit.id)
        ).mappings().all()
        return tuple(RefundNatureAuditVO(**row) for row in rows)

    def command_bills(self, bill_ids: list[int]) -> dict[int, RefundCommandBillVO]:
        if not bill_ids:
            return {}
        rows = self.db.execute(
            select(Bill.id, Bill.amount, Bill.aggregate_excluded)
            .where(Bill.id.in_(bill_ids))
        ).mappings().all()
        return {row["id"]: RefundCommandBillVO(**row) for row in rows}

    def allocation(self, allocation_id: int) -> RefundAllocationCommandVO | None:
        row = self.db.execute(
            self._allocation_statement().where(RefundAllocation.id == allocation_id)
        ).mappings().one_or_none()
        return RefundAllocationCommandVO(**row) if row else None

    def allocation_by_idempotency_key(self, key: str) -> RefundAllocationCommandVO | None:
        row = self.db.execute(
            self._allocation_statement().where(RefundAllocation.idempotency_key == key)
        ).mappings().one_or_none()
        return RefundAllocationCommandVO(**row) if row else None

    def confirmed_allocation_rows(
        self,
        *,
        refund_bill_id: int,
        expense_bill_id: int,
    ) -> tuple[tuple[int, int, float], ...]:
        rows = self.db.execute(
            select(
                RefundAllocation.refund_bill_id,
                RefundAllocation.expense_bill_id,
                RefundAllocation.amount,
            ).where(
                RefundAllocation.status == "confirmed",
                (
                    (RefundAllocation.refund_bill_id == refund_bill_id)
                    | (RefundAllocation.expense_bill_id == expense_bill_id)
                ),
            )
        ).all()
        return tuple((row.refund_bill_id, row.expense_bill_id, row.amount) for row in rows)

    def create_allocation(
        self,
        *,
        refund_bill_id: int,
        expense_bill_id: int,
        amount: float,
        idempotency_key: str,
        request_payload: str,
        created_at: datetime,
    ) -> RefundAllocationCommandVO:
        allocation = RefundAllocation(
            refund_bill_id=refund_bill_id,
            expense_bill_id=expense_bill_id,
            amount=amount,
            status="confirmed",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            created_at=created_at,
        )
        self.db.add(allocation)
        self.db.flush()
        return RefundAllocationCommandVO(
            id=allocation.id,
            refund_bill_id=refund_bill_id,
            expense_bill_id=expense_bill_id,
            amount=amount,
            status="confirmed",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            created_at=created_at,
            revoked_at=None,
        )

    def revoke_allocation(self, allocation_id: int, revoked_at: datetime) -> None:
        self.db.execute(
            update(RefundAllocation)
            .where(RefundAllocation.id == allocation_id)
            .values(status="revoked", revoked_at=revoked_at)
        )

    def allocation_audit_by_idempotency_key(self, key: str) -> RefundAllocationAuditVO | None:
        row = self.db.execute(
            self._allocation_audit_statement()
            .where(RefundAllocationAudit.idempotency_key == key)
        ).mappings().one_or_none()
        return RefundAllocationAuditVO(**row) if row else None

    def latest_confirmation_audit(self, allocation_id: int) -> RefundAllocationAuditVO | None:
        row = self.db.execute(
            self._allocation_audit_statement()
            .where(
                RefundAllocationAudit.allocation_id == allocation_id,
                RefundAllocationAudit.action == "confirm",
            )
            .order_by(RefundAllocationAudit.id.desc())
            .limit(1)
        ).mappings().one_or_none()
        return RefundAllocationAuditVO(**row) if row else None

    def allocation_audits(self, allocation_id: int) -> tuple[RefundAllocationAuditVO, ...]:
        rows = self.db.execute(
            self._allocation_audit_statement()
            .where(RefundAllocationAudit.allocation_id == allocation_id)
            .order_by(RefundAllocationAudit.id)
        ).mappings().all()
        return tuple(RefundAllocationAuditVO(**row) for row in rows)

    def append_allocation_audit(
        self,
        *,
        allocation_id: int,
        action: str,
        reason: str,
        before_state: str,
        after_state: str,
        reverses_audit_id: int | None = None,
        idempotency_key: str | None = None,
        request_payload: str = "",
        created_at: datetime,
    ) -> None:
        self.db.execute(insert(RefundAllocationAudit).values(
            allocation_id=allocation_id,
            action=action,
            actor="local-user",
            reason=reason,
            before_state=before_state,
            after_state=after_state,
            reverses_audit_id=reverses_audit_id,
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            created_at=created_at,
        ))

    def designation_exists(self, bill_id: int) -> bool:
        return self.db.scalar(
            select(RefundDesignation.bill_id).where(RefundDesignation.bill_id == bill_id)
        ) is not None

    def set_designation(self, bill_id: int, *, designated: bool, created_at: datetime) -> None:
        if designated:
            self.db.execute(insert(RefundDesignation).values(
                bill_id=bill_id,
                created_at=created_at,
            ))
        else:
            self.db.execute(delete(RefundDesignation).where(RefundDesignation.bill_id == bill_id))

    def latest_nature_audit_id(self, bill_id: int) -> int:
        return self.db.scalar(
            select(func.max(RefundNatureAudit.id)).where(RefundNatureAudit.bill_id == bill_id)
        ) or 0

    def nature_audit_by_idempotency_key(self, key: str) -> RefundNatureCommandAuditVO | None:
        row = self.db.execute(
            select(
                RefundNatureAudit.id,
                RefundNatureAudit.bill_id,
                RefundNatureAudit.after_nature,
                RefundNatureAudit.idempotency_key,
                RefundNatureAudit.request_payload,
            ).where(RefundNatureAudit.idempotency_key == key)
        ).mappings().one_or_none()
        return RefundNatureCommandAuditVO(**row) if row else None

    def has_confirmed_refund_allocation(self, bill_id: int) -> bool:
        return self.db.scalar(
            select(RefundAllocation.id)
            .where(
                RefundAllocation.refund_bill_id == bill_id,
                RefundAllocation.status == "confirmed",
            )
            .limit(1)
        ) is not None

    def append_nature_audit(
        self,
        *,
        bill_id: int,
        action: str,
        reason: str,
        before_nature: str,
        after_nature: str,
        idempotency_key: str | None,
        request_payload: str,
        created_at: datetime,
    ) -> int:
        audit = RefundNatureAudit(
            bill_id=bill_id,
            action=action,
            reason=reason,
            actor="local-user",
            before_nature=before_nature,
            after_nature=after_nature,
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            created_at=created_at,
        )
        self.db.add(audit)
        self.db.flush()
        return audit.id

    @staticmethod
    def _allocation_statement():
        return select(
            RefundAllocation.id,
            RefundAllocation.refund_bill_id,
            RefundAllocation.expense_bill_id,
            RefundAllocation.amount,
            RefundAllocation.status,
            RefundAllocation.idempotency_key,
            RefundAllocation.request_payload,
            RefundAllocation.created_at,
            RefundAllocation.revoked_at,
        )

    @staticmethod
    def _allocation_audit_statement():
        return select(
            RefundAllocationAudit.id,
            RefundAllocationAudit.allocation_id,
            RefundAllocationAudit.action,
            RefundAllocationAudit.actor,
            RefundAllocationAudit.reason,
            RefundAllocationAudit.before_state,
            RefundAllocationAudit.after_state,
            RefundAllocationAudit.reverses_audit_id,
            RefundAllocationAudit.idempotency_key,
            RefundAllocationAudit.request_payload,
            RefundAllocationAudit.created_at,
        )
