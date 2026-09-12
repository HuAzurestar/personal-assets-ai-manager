from __future__ import annotations

import json
from dataclasses import asdict

from sqlalchemy.orm import Session

from app.mappers.target_migration_mapper import TargetMigrationMapper
from app.money import cents
from app.schemas.migration import (
    BillFactShadowVO,
    LegacyRefundAllocationAuditVO,
    LegacyRefundAllocationVO,
    LegacyRefundNatureAuditVO,
    ReviewCaseBillShadowVO,
    ReviewCaseShadowVO,
    ReviewHistoryShadowVO,
    ReviewShadowReport,
)
from app.services.review_migration_service import (
    CHILD_ID_BITS,
    MAX_CHILD_ID,
    ReviewMatterShadowMigrationService,
)


REFUND_CASE_START = 3 << 40
REFUND_CASE_END = 4 << 40


class RefundReviewShadowMigrationService:
    """Unify refund nature and allocations as one case per refund inflow."""

    def __init__(self, db: Session):
        self.db = db
        self.mapper = TargetMigrationMapper(db)

    def backfill_and_compare(self) -> ReviewShadowReport:
        blockers: list[str] = []
        allocations = self.mapper.legacy_refund_allocations()
        allocation_audits = self.mapper.legacy_refund_allocation_audits()
        designations = self.mapper.legacy_refund_designations()
        nature_audits = self.mapper.legacy_refund_nature_audits()
        bill_ids = sorted({
            *(allocation.refund_bill_id for allocation in allocations),
            *(allocation.expense_bill_id for allocation in allocations),
            *(designation.bill_id for designation in designations),
            *(audit.bill_id for audit in nature_audits),
        })
        facts = self.mapper.target_bill_facts(bill_ids)
        expected_cases, expected_lines, expected_history = self._expected(
            allocations,
            allocation_audits,
            {designation.bill_id for designation in designations},
            nature_audits,
            facts,
            blockers,
        )
        current_cases, current_lines, current_history = self._current()
        case_inserts = self._missing(expected_cases, current_cases)
        line_inserts = self._missing(expected_lines, current_lines)
        history_inserts = ReviewMatterShadowMigrationService._missing_history(
            expected_history,
            current_history,
            blockers,
        )
        try:
            self.mapper.insert_review_cases([asdict(row) for row in case_inserts])
            self.mapper.insert_review_case_bills([asdict(row) for row in line_inserts])
            self.mapper.insert_review_history([asdict(row) for row in history_inserts])
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        actual_cases, actual_lines, actual_history = self._current()
        support = ReviewMatterShadowMigrationService
        case_report = support._table_report(expected_cases, actual_cases, len(case_inserts))
        line_report = support._table_report(expected_lines, actual_lines, len(line_inserts))
        history_report = support._table_report(
            expected_history,
            actual_history,
            len(history_inserts),
        )
        return ReviewShadowReport(
            matched=(
                not blockers
                and not case_report.mismatched_ids
                and not line_report.mismatched_ids
                and not history_report.mismatched_ids
            ),
            review_case=case_report,
            review_case_bill=line_report,
            review_history=history_report,
            blockers=blockers,
        )

    def _current(self):
        query = {"case_id_start": REFUND_CASE_START, "case_id_end": REFUND_CASE_END}
        return (
            self.mapper.target_review_cases(**query),
            self.mapper.target_review_case_bills(**query),
            self.mapper.target_review_history(**query),
        )

    @classmethod
    def _expected(
        cls,
        allocations: tuple[LegacyRefundAllocationVO, ...],
        allocation_audits: tuple[LegacyRefundAllocationAuditVO, ...],
        designated_bill_ids: set[int],
        nature_audits: tuple[LegacyRefundNatureAuditVO, ...],
        facts: tuple[BillFactShadowVO, ...],
        blockers: list[str],
    ):
        fact_by_id = {fact.id: fact for fact in facts}
        allocation_by_id = {allocation.id: allocation for allocation in allocations}
        allocations_by_refund: dict[int, list[LegacyRefundAllocationVO]] = {}
        audits_by_allocation: dict[int, list[LegacyRefundAllocationAuditVO]] = {}
        nature_by_refund: dict[int, list[LegacyRefundNatureAuditVO]] = {}
        for allocation in allocations:
            allocations_by_refund.setdefault(allocation.refund_bill_id, []).append(allocation)
        for audit in allocation_audits:
            allocation = allocation_by_id.get(audit.allocation_id)
            if not allocation:
                blockers.append(
                    f"refund allocation audit {audit.id}: missing allocation {audit.allocation_id}"
                )
                continue
            audits_by_allocation.setdefault(audit.allocation_id, []).append(audit)
        for audit in nature_audits:
            nature_by_refund.setdefault(audit.bill_id, []).append(audit)

        refund_ids = sorted(
            set(allocations_by_refund) | set(nature_by_refund) | designated_bill_ids
        )
        cls._validate_global_allocations(allocations, fact_by_id, blockers)
        cases: list[ReviewCaseShadowVO] = []
        lines: list[ReviewCaseBillShadowVO] = []
        histories: list[ReviewHistoryShadowVO] = []
        for refund_id in refund_ids:
            if refund_id <= 0 or refund_id >= (1 << 40):
                blockers.append(f"refund bill {refund_id}: ID exceeds migration namespace")
                continue
            refund_fact = fact_by_id.get(refund_id)
            if not refund_fact:
                blockers.append(f"refund bill {refund_id}: missing bill_fact")
                continue
            if refund_fact.cash_direction != "IN":
                blockers.append(f"refund bill {refund_id}: refund fact is not an inflow")
                continue
            refund_allocations = allocations_by_refund.get(refund_id, [])
            invalid = False
            for allocation in refund_allocations:
                if allocation.id not in audits_by_allocation:
                    blockers.append(
                        f"refund allocation {allocation.id}: missing immutable audit history"
                    )
                    invalid = True
            refund_nature_audits = nature_by_refund.get(refund_id, [])
            if not refund_nature_audits:
                blockers.append(f"refund bill {refund_id}: missing nature audit history")
                continue
            if invalid:
                continue

            events: list[tuple[object, int, int]] = []
            events.extend((audit, 0, audit.id) for audit in refund_nature_audits)
            for allocation in refund_allocations:
                events.extend(
                    (audit, 1, audit.id)
                    for audit in audits_by_allocation.get(allocation.id, [])
                )
            events.sort(key=lambda item: (item[0].created_at, item[1], item[2]))
            if not events or len(events) >= MAX_CHILD_ID:
                blockers.append(f"refund bill {refund_id}: invalid history length {len(events)}")
                continue

            state: dict[str, object] = {"nature": "ordinary", "allocations": {}}
            case_id = REFUND_CASE_START + refund_id
            audit_history_ids: dict[int, int] = {}
            case_history: list[ReviewHistoryShadowVO] = []
            case_valid = True
            for version, (event, _priority, _source_id) in enumerate(events, start=1):
                before_snapshot, _, _, _ = cls._aggregate(
                    refund_fact,
                    refund_allocations,
                    fact_by_id,
                    state,
                    version - 1,
                    blockers,
                )
                if isinstance(event, LegacyRefundNatureAuditVO):
                    if (
                        event.before_nature != state["nature"]
                        or event.after_nature not in {"ordinary", "refund"}
                        or event.action != event.after_nature
                    ):
                        blockers.append(
                            f"refund nature audit {event.id}: state transition is not contiguous"
                        )
                        case_valid = False
                        break
                    state["nature"] = event.after_nature
                    operation = (
                        "CREATE" if version == 1
                        else "REVOKE" if event.after_nature == "ordinary"
                        else "RESTORE"
                    )
                    request_source = event.request_payload
                    idempotency = cls._idempotency_key(
                        "refund-nature",
                        event.idempotency_key or "",
                    )
                    actor = event.actor
                    reason = event.reason
                    reverses_history_id = 0
                else:
                    allocation = allocation_by_id[event.allocation_id]
                    before_state = cls._parse_object(
                        event.before_state,
                        f"refund allocation audit {event.id} before_state",
                        blockers,
                    )
                    after_state = cls._parse_object(
                        event.after_state,
                        f"refund allocation audit {event.id} after_state",
                        blockers,
                    )
                    if before_state is None or after_state is None:
                        case_valid = False
                        break
                    current = state["allocations"].get(str(allocation.id))
                    expected_after = {"confirm": "confirmed", "revoke": "revoked"}.get(
                        event.action
                    )
                    if (
                        before_state.get("status") != current
                        or after_state.get("status") != expected_after
                    ):
                        blockers.append(
                            f"refund allocation audit {event.id}: state transition is not contiguous"
                        )
                        case_valid = False
                        break
                    if "amount" in after_state:
                        try:
                            snapshot_amount = abs(cents(after_state["amount"]))
                            allocation_amount = abs(cents(allocation.amount))
                        except ValueError:
                            blockers.append(
                                f"refund allocation audit {event.id}: invalid amount snapshot"
                            )
                            case_valid = False
                            break
                        if snapshot_amount != allocation_amount:
                            blockers.append(
                                f"refund allocation audit {event.id}: amount differs from allocation"
                            )
                            case_valid = False
                            break
                    state["allocations"][str(allocation.id)] = after_state["status"]
                    operation = (
                        "CREATE" if version == 1
                        else "CONFIRM" if after_state["status"] == "confirmed"
                        else "REVOKE"
                    )
                    request_source = event.request_payload or (
                        allocation.request_payload if after_state["status"] == "confirmed" else ""
                    )
                    source_key = event.idempotency_key or (
                        allocation.idempotency_key if after_state["status"] == "confirmed" else ""
                    )
                    idempotency = cls._idempotency_key("refund-allocation", source_key)
                    actor = event.actor
                    reason = event.reason
                    reverses_history_id = 0
                    if event.reverses_audit_id is not None:
                        reverses_history_id = audit_history_ids.get(event.reverses_audit_id, 0)
                        if not reverses_history_id:
                            blockers.append(
                                f"refund allocation audit {event.id}: missing reversed audit {event.reverses_audit_id}"
                            )
                            case_valid = False
                            break
                request_json = cls._canonical_json_text(
                    request_source,
                    f"refund history event {getattr(event, 'id')} request",
                    blockers,
                )
                if request_json is None:
                    case_valid = False
                    break
                after_snapshot, _, _, _ = cls._aggregate(
                    refund_fact,
                    refund_allocations,
                    fact_by_id,
                    state,
                    version,
                    blockers,
                )
                history_id = cls._child_id(case_id, version)
                case_history.append(ReviewHistoryShadowVO(
                    id=history_id,
                    created_time=event.created_at,
                    updated_time=event.created_at,
                    case_id=case_id,
                    version=version,
                    operation=operation,
                    schema_version=1,
                    request_json=request_json,
                    before_json=before_snapshot,
                    after_json=after_snapshot,
                    snapshot_hash=cls._sha256(after_snapshot),
                    reverses_history_id=reverses_history_id,
                    actor=actor or "local-user",
                    reason=reason or "",
                    idempotency_key=idempotency,
                ))
                if isinstance(event, LegacyRefundAllocationAuditVO):
                    audit_history_ids[event.id] = history_id
            if not case_valid:
                continue

            final_allocations = state["allocations"]
            source_statuses = {
                str(allocation.id): allocation.status for allocation in refund_allocations
            }
            if final_allocations != source_statuses:
                blockers.append(
                    f"refund bill {refund_id}: allocation rows differ from latest audit states"
                )
                continue
            designated = refund_id in designated_bill_ids
            if designated != (state["nature"] == "refund"):
                blockers.append(
                    f"refund bill {refund_id}: designation differs from latest nature audit"
                )
                continue
            final_snapshot, line_values, allocation_status, status = cls._aggregate(
                refund_fact,
                refund_allocations,
                fact_by_id,
                state,
                len(events),
                blockers,
            )
            current_result = json.loads(final_snapshot)["result"]
            first_time = events[0][0].created_at
            last_time = events[-1][0].created_at
            cases.append(ReviewCaseShadowVO(
                id=case_id,
                created_time=first_time,
                updated_time=last_time,
                review_type="REFUND",
                status=status,
                allocation_status=allocation_status,
                version=len(events),
                title=f"Refund: {refund_fact.counterparty}",
                result_json=cls._canonical(current_result),
            ))
            for position, values in enumerate(line_values, start=1):
                lines.append(ReviewCaseBillShadowVO(
                    id=cls._child_id(case_id, position),
                    created_time=last_time,
                    updated_time=last_time,
                    case_id=case_id,
                    **values,
                ))
            histories.extend(case_history)
        return tuple(cases), tuple(lines), tuple(histories)

    @classmethod
    def _aggregate(
        cls,
        refund_fact: BillFactShadowVO,
        allocations: list[LegacyRefundAllocationVO],
        facts: dict[int, BillFactShadowVO],
        state: dict[str, object],
        version: int,
        blockers: list[str],
    ):
        active = [
            allocation for allocation in allocations
            if state["allocations"].get(str(allocation.id)) == "confirmed"
        ]
        refund_cents = cls._fact_cents(refund_fact)
        allocated = 0
        line_values = [{
            "bill_id": refund_fact.id,
            "role": "REFUND_RECEIVED",
            "party": "",
            "amount_value": refund_fact.amount_value,
            "amount_scale": refund_fact.amount_scale,
            "currency_code": refund_fact.currency_code,
        }]
        allocation_status = "PARTIAL"
        for allocation in active:
            expense = facts.get(allocation.expense_bill_id)
            if not expense:
                allocation_status = "CONFLICT"
                continue
            try:
                amount_cents = abs(cents(allocation.amount))
            except ValueError:
                blockers.append(f"refund allocation {allocation.id}: invalid amount")
                allocation_status = "CONFLICT"
                continue
            allocated += amount_cents
            line_values.append({
                "bill_id": expense.id,
                "role": "REFUND_EXPENSE",
                "party": "",
                "amount_value": amount_cents,
                "amount_scale": 2,
                "currency_code": "CNY",
            })
        if refund_cents is None or allocated > refund_cents:
            allocation_status = "CONFLICT"
        elif allocated == refund_cents:
            allocation_status = "COMPLETE"
        if state["nature"] == "ordinary" and active:
            allocation_status = "CONFLICT"
        status = "CONFIRMED" if state["nature"] == "refund" else "REVOKED"
        result = {
            "confirmed_allocation_count": len(active),
            "legacy_nature": state["nature"],
            "total_allocation_count": len(allocations),
        }
        snapshot = cls._canonical({
            "allocation_status": allocation_status,
            "lines": line_values,
            "result": result,
            "review_type": "REFUND",
            "status": status,
            "title": f"Refund: {refund_fact.counterparty}",
            "version": version,
        })
        return snapshot, line_values, allocation_status, status

    @classmethod
    def _validate_global_allocations(cls, allocations, facts, blockers):
        refund_totals: dict[int, int] = {}
        expense_totals: dict[int, int] = {}
        for allocation in allocations:
            refund = facts.get(allocation.refund_bill_id)
            expense = facts.get(allocation.expense_bill_id)
            if not refund or not expense:
                continue
            if refund.cash_direction != "IN" or expense.cash_direction != "OUT":
                blockers.append(f"refund allocation {allocation.id}: bill directions are invalid")
                continue
            if refund.currency_code != "CNY" or expense.currency_code != "CNY":
                blockers.append(f"refund allocation {allocation.id}: legacy allocation is not CNY")
                continue
            if allocation.status != "confirmed":
                continue
            try:
                amount = abs(cents(allocation.amount))
            except ValueError:
                blockers.append(f"refund allocation {allocation.id}: invalid amount")
                continue
            refund_totals[refund.id] = refund_totals.get(refund.id, 0) + amount
            expense_totals[expense.id] = expense_totals.get(expense.id, 0) + amount
        for bill_id, amount in {**refund_totals, **expense_totals}.items():
            fact_amount = cls._fact_cents(facts[bill_id])
            total = (
                refund_totals.get(bill_id, 0) + expense_totals.get(bill_id, 0)
            )
            if fact_amount is None or total > fact_amount:
                blockers.append(f"bill {bill_id}: confirmed refund allocation exceeds fact amount")

    @staticmethod
    def _fact_cents(fact: BillFactShadowVO) -> int | None:
        return ReviewMatterShadowMigrationService._fact_cents(fact)

    @staticmethod
    def _parse_object(raw: str, label: str, blockers: list[str]):
        return ReviewMatterShadowMigrationService._parse_object(raw, label, blockers)

    @staticmethod
    def _canonical_json_text(raw: str, label: str, blockers: list[str]):
        return ReviewMatterShadowMigrationService._canonical_json_text(raw, label, blockers)

    @staticmethod
    def _canonical(value: object) -> str:
        return ReviewMatterShadowMigrationService._canonical(value)

    @staticmethod
    def _sha256(value: str) -> str:
        return ReviewMatterShadowMigrationService._sha256(value)

    @classmethod
    def _idempotency_key(cls, namespace: str, source: str) -> str:
        if not source:
            return ""
        value = f"{namespace}:{source}"
        return value if len(value) <= 120 else f"{namespace}:sha256:{cls._sha256(source)}"

    @staticmethod
    def _child_id(case_id: int, child: int) -> int:
        return (case_id << CHILD_ID_BITS) | child

    @staticmethod
    def _missing(expected, current):
        current_ids = {row.id for row in current}
        return tuple(row for row in expected if row.id not in current_ids)
