from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.errors import RefundCommandError
from app.mappers.refund_mapper import RefundMapper
from app.money import cents, money
from app.schemas import NatureRequest, RefundAllocationCreate, RefundAllocationRead, UndoRequest
from app.schemas.refund import (
    RefundAllocationAuditRead,
    RefundListItemRead,
    RefundBillSummaryRead,
    RefundNatureCommandRead,
    RefundNatureRead,
    RefundNatureAuditRead,
    RefundPageRead,
    RefundSummaryRead,
)
from app.services.ledger_service import bill_read_from_vo
from app.services.matter_service import MatterService


class RefundService:
    def __init__(self, db: Session):
        self.db = db
        self.mapper = RefundMapper(db)

    def list(self) -> list[RefundListItemRead]:
        bill_ids = self.mapper.designated_bill_ids()
        if not bill_ids:
            return []
        return self._details(bill_ids)

    def page(self, *, page: int, page_size: int) -> RefundPageRead:
        total, bill_ids = self.mapper.designated_page(page=page, page_size=page_size)
        if not bill_ids:
            return RefundPageRead(items=[], total=total, page=page, page_size=page_size)
        bills = self.mapper.summary_bills(bill_ids)
        allocations = self.mapper.allocations(bill_ids)
        allocations_by_bill = self._allocations_by_bill(allocations)
        items = []
        for bill_id in bill_ids:
            bill = self._bill_or_error(bills, bill_id)
            bill_allocations = allocations_by_bill.get(bill_id, [])
            allocated_cents = sum(
                cents(allocation.amount)
                for allocation in bill_allocations
                if allocation.status == "confirmed"
            )
            items.append(RefundSummaryRead(
                bill=RefundBillSummaryRead(
                    id=bill.id,
                    occurred_at=bill.occurred_at,
                    merchant=bill.merchant,
                    amount=bill.amount,
                    currency=bill.currency,
                    account_name=bill.account_name,
                ),
                unallocated=money(cents(bill.amount) - allocated_cents),
                allocated=money(allocated_cents),
                allocation_count=len(bill_allocations),
                confirmed_allocation_count=sum(
                    allocation.status == "confirmed"
                    for allocation in bill_allocations
                ),
            ))
        return RefundPageRead(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )

    def get(self, bill_id: int) -> RefundListItemRead:
        if not self.mapper.is_designated(bill_id):
            raise LookupError("退款记录不存在")
        return self._details([bill_id])[0]

    def create_allocation(self, payload: RefundAllocationCreate) -> RefundAllocationRead:
        self.mapper.begin_immediate()
        try:
            request_payload = json.dumps(
                payload.model_dump(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            existing = self.mapper.allocation_by_idempotency_key(payload.idempotency_key)
            if existing:
                if existing.request_payload != request_payload or existing.status != "confirmed":
                    raise RefundCommandError(
                        409,
                        "Idempotency key was already used for a different refund allocation",
                    )
                self.db.commit()
                return self._allocation_read(existing)

            bills = self.mapper.command_bills([
                payload.refund_bill_id,
                payload.expense_bill_id,
            ])
            refund = bills.get(payload.refund_bill_id)
            expense = bills.get(payload.expense_bill_id)
            if not refund or not expense:
                raise RefundCommandError(
                    422,
                    "Refund and expense transactions must both exist",
                )
            if refund.amount <= 0 or expense.amount >= 0:
                raise RefundCommandError(
                    422,
                    "Refund must be positive and original expense must be negative",
                )
            if refund.aggregate_excluded or expense.aggregate_excluded:
                raise RefundCommandError(
                    422,
                    "Refund allocations require effective ledger transactions",
                )
            conflicts = MatterService(self.db).conflicting_matter_ids({refund.id, expense.id})
            if conflicts:
                raise RefundCommandError(
                    409,
                    f"流水已用于手工事项 {conflicts}；请先修改或撤销相关分配",
                )

            rows = self.mapper.confirmed_allocation_rows(
                refund_bill_id=refund.id,
                expense_bill_id=expense.id,
            )
            allocated_refund = sum(
                cents(amount)
                for refund_id, _expense_id, amount in rows
                if refund_id == refund.id
            )
            allocated_expense = sum(
                cents(amount)
                for _refund_id, expense_id, amount in rows
                if expense_id == expense.id
            )
            allocation_cents = cents(payload.amount)
            if allocated_refund + allocation_cents > cents(refund.amount):
                raise RefundCommandError(
                    422,
                    "Refund allocation exceeds the available refund amount",
                )
            if allocated_expense + allocation_cents > abs(cents(expense.amount)):
                raise RefundCommandError(
                    422,
                    "Refund allocation exceeds the original expense amount",
                )

            now = datetime.now()
            allocation = self.mapper.create_allocation(
                refund_bill_id=refund.id,
                expense_bill_id=expense.id,
                amount=payload.amount,
                idempotency_key=payload.idempotency_key,
                request_payload=request_payload,
                created_at=now,
            )
            if not self.mapper.designation_exists(refund.id):
                self.mapper.set_designation(refund.id, designated=True, created_at=now)
                self.mapper.append_nature_audit(
                    bill_id=refund.id,
                    action="refund",
                    reason=payload.reason or "确认退款分配",
                    before_nature="ordinary",
                    after_nature="refund",
                    idempotency_key=None,
                    request_payload="",
                    created_at=now,
                )
            self.mapper.append_allocation_audit(
                allocation_id=allocation.id,
                action="confirm",
                reason=payload.reason,
                before_state=json.dumps({"status": None}),
                after_state=json.dumps({"status": "confirmed", "amount": allocation.amount}),
                created_at=now,
            )
            self.db.commit()
            return self._allocation_read(allocation)
        except Exception:
            self.db.rollback()
            raise

    def undo_allocation(
        self,
        allocation_id: int,
        payload: UndoRequest,
    ) -> RefundAllocationRead:
        self.mapper.begin_immediate()
        try:
            request_payload = json.dumps(
                {"allocation_id": allocation_id, **payload.model_dump()},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if payload.idempotency_key:
                existing = self.mapper.allocation_audit_by_idempotency_key(
                    payload.idempotency_key
                )
                if existing:
                    if (
                        existing.allocation_id != allocation_id
                        or existing.action != "revoke"
                        or existing.request_payload != request_payload
                    ):
                        raise RefundCommandError(409, "退款撤销请求已用于其他操作")
                    allocation = self.mapper.allocation(allocation_id)
                    if not allocation or allocation.status != "revoked":
                        raise RefundCommandError(409, "退款分配状态已发生变化，请刷新后重试")
                    self.db.commit()
                    return self._allocation_read(allocation)

            allocation = self.mapper.allocation(allocation_id)
            if not allocation:
                raise RefundCommandError(404, "Refund allocation not found")
            if allocation.status != "confirmed":
                raise RefundCommandError(409, "Refund allocation has already been revoked")
            confirmation = self.mapper.latest_confirmation_audit(allocation_id)
            now = datetime.now()
            self.mapper.revoke_allocation(allocation_id, now)
            self.mapper.append_allocation_audit(
                allocation_id=allocation.id,
                action="revoke",
                reason=payload.reason,
                before_state=json.dumps({"status": "confirmed", "amount": allocation.amount}),
                after_state=json.dumps({"status": "revoked", "amount": allocation.amount}),
                reverses_audit_id=confirmation.id if confirmation else None,
                idempotency_key=payload.idempotency_key,
                request_payload=request_payload,
                created_at=now,
            )
            self.db.commit()
            return RefundAllocationRead(
                id=allocation.id,
                refund_bill_id=allocation.refund_bill_id,
                expense_bill_id=allocation.expense_bill_id,
                amount=allocation.amount,
                status="revoked",
                idempotency_key=allocation.idempotency_key,
                created_at=allocation.created_at,
                revoked_at=now,
            )
        except Exception:
            self.db.rollback()
            raise

    def allocation_audits(self, allocation_id: int) -> list[RefundAllocationAuditRead]:
        if not self.mapper.allocation(allocation_id):
            raise RefundCommandError(404, "Refund allocation not found")
        return [RefundAllocationAuditRead(
            id=audit.id,
            action=audit.action,
            actor=audit.actor,
            reason=audit.reason,
            before_state=json.loads(audit.before_state),
            after_state=json.loads(audit.after_state),
            reverses_audit_id=audit.reverses_audit_id,
            idempotency_key=audit.idempotency_key,
            created_at=audit.created_at,
        ) for audit in self.mapper.allocation_audits(allocation_id)]

    def set_nature(self, bill_id: int, payload: NatureRequest) -> RefundNatureCommandRead:
        self.mapper.begin_immediate()
        try:
            bill = self.mapper.command_bills([bill_id]).get(bill_id)
            if not bill:
                raise RefundCommandError(404, "流水不存在")
            request_payload = json.dumps(
                {"bill_id": bill_id, **payload.model_dump()},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if payload.idempotency_key:
                existing = self.mapper.nature_audit_by_idempotency_key(
                    payload.idempotency_key
                )
                if existing:
                    latest = self.mapper.latest_nature_audit_id(bill_id)
                    if (
                        existing.bill_id != bill_id
                        or existing.request_payload != request_payload
                        or existing.id != latest
                    ):
                        raise RefundCommandError(
                            409,
                            "退款性质请求或当前状态已发生变化，请刷新后重试",
                        )
                    self.db.commit()
                    return RefundNatureCommandRead(
                        bill_id=bill_id,
                        nature=existing.after_nature,
                        audit_id=existing.id,
                    )

            latest = self.mapper.latest_nature_audit_id(bill_id)
            if latest != payload.expected_audit_id:
                raise RefundCommandError(409, "退款性质已有变化，请刷新后重试")
            if bill.amount <= 0 or bill.aggregate_excluded:
                raise RefundCommandError(422, "仅能对未被排除的正向流水确认退款性质")
            conflicts = MatterService(self.db).conflicting_matter_ids({bill_id})
            if conflicts:
                raise RefundCommandError(
                    409,
                    f"流水已用于手工事项 {conflicts}；请先修改或撤销相关分配",
                )
            designated = self.mapper.designation_exists(bill_id)
            before_nature = "refund" if designated else "ordinary"
            if payload.nature == "ordinary":
                if self.mapper.has_confirmed_refund_allocation(bill_id):
                    raise RefundCommandError(409, "请先撤销有效退款分配，再修改退款性质")
                if designated:
                    self.mapper.set_designation(
                        bill_id,
                        designated=False,
                        created_at=datetime.now(),
                    )
            elif not designated:
                self.mapper.set_designation(
                    bill_id,
                    designated=True,
                    created_at=datetime.now(),
                )
            now = datetime.now()
            audit_id = self.mapper.append_nature_audit(
                bill_id=bill_id,
                action=payload.nature,
                reason=payload.reason,
                before_nature=before_nature,
                after_nature=payload.nature,
                idempotency_key=payload.idempotency_key,
                request_payload=request_payload,
                created_at=now,
            )
            self.db.commit()
            return RefundNatureCommandRead(
                bill_id=bill_id,
                nature=payload.nature,
                audit_id=audit_id,
            )
        except Exception:
            self.db.rollback()
            raise

    def get_nature(self, bill_id: int) -> RefundNatureRead:
        if bill_id not in self.mapper.command_bills([bill_id]):
            raise RefundCommandError(404, "流水不存在")
        return RefundNatureRead(
            nature="refund" if self.mapper.designation_exists(bill_id) else "ordinary",
            audit_id=self.mapper.latest_nature_audit_id(bill_id),
        )

    def _details(self, bill_ids: list[int]) -> list[RefundListItemRead]:
        bills = self.mapper.bills(bill_ids)
        allocations = self.mapper.allocations(bill_ids)
        histories = self.mapper.nature_audits(bill_ids)

        allocations_by_bill = self._allocations_by_bill(allocations)
        histories_by_bill = {}
        for audit in histories:
            histories_by_bill.setdefault(audit.bill_id, []).append(audit)

        result = []
        for bill_id in bill_ids:
            bill = self._bill_or_error(bills, bill_id)
            bill_allocations = allocations_by_bill.get(bill_id, [])
            history = histories_by_bill.get(bill_id, [])
            allocated = sum(
                cents(allocation.amount)
                for allocation in bill_allocations
                if allocation.status == "confirmed"
            )
            result.append(RefundListItemRead(
                bill=bill_read_from_vo(bill),
                unallocated=money(cents(bill.amount) - allocated),
                allocations=[RefundAllocationRead(
                    id=allocation.id,
                    refund_bill_id=allocation.refund_bill_id,
                    expense_bill_id=allocation.expense_bill_id,
                    amount=allocation.amount,
                    status=allocation.status,
                    idempotency_key=allocation.idempotency_key,
                    created_at=allocation.created_at,
                    revoked_at=allocation.revoked_at,
                ) for allocation in bill_allocations],
                nature_audit_id=history[-1].id if history else 0,
                history=[RefundNatureAuditRead(
                    id=audit.id,
                    action=audit.action,
                    reason=audit.reason,
                    actor=audit.actor,
                    before_nature=audit.before_nature,
                    after_nature=audit.after_nature,
                    idempotency_key=audit.idempotency_key,
                    created_at=audit.created_at,
                ) for audit in history],
            ))
        return result

    @staticmethod
    def _allocations_by_bill(allocations):
        result = {}
        for allocation in allocations:
            result.setdefault(allocation.refund_bill_id, []).append(allocation)
        return result

    @staticmethod
    def _allocation_read(allocation) -> RefundAllocationRead:
        return RefundAllocationRead(
            id=allocation.id,
            refund_bill_id=allocation.refund_bill_id,
            expense_bill_id=allocation.expense_bill_id,
            amount=allocation.amount,
            status=allocation.status,
            idempotency_key=allocation.idempotency_key,
            created_at=allocation.created_at,
            revoked_at=allocation.revoked_at,
        )

    @staticmethod
    def _bill_or_error(bills, bill_id):
        bill = bills.get(bill_id)
        if not bill:
            raise RuntimeError(f"Refund designation references missing bill {bill_id}")
        return bill
