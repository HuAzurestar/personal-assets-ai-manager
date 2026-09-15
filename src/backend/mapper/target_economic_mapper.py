from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from backend.entity import (
    BillFact,
    LedgerEntry,
    LedgerEntrySource,
    ReviewCase,
    ReviewCaseBill,
    ReviewHistory,
)
from backend.schema.target_review import (
    TargetEconomicFlowRead,
    TargetEconomicReviewRead,
    TargetFlowAllocationRead,
    TargetReviewFactVO,
    TargetReviewHistoryRead,
)


class TargetEconomicMapper:
    """Set-oriented persistence for v2 Review/Economic/Allocation commands."""

    def __init__(self, db: Session):
        self.db = db

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def facts(self, fact_ids: list[int]) -> tuple[TargetReviewFactVO, ...]:
        if not fact_ids:
            return ()
        rows = self.db.execute(select(
            BillFact.id,
            BillFact.occurred_time,
            BillFact.cash_direction,
            BillFact.amount_value,
            BillFact.amount_scale,
            BillFact.currency_code,
            BillFact.account_code,
            BillFact.counterparty,
            BillFact.summary,
            BillFact.fact_key,
            BillFact.created_time,
            BillFact.updated_time,
        ).where(BillFact.id.in_(fact_ids))).mappings().all()
        return tuple(TargetReviewFactVO(**row) for row in rows)

    def all_fact_ids(self) -> list[int]:
        return list(self.db.scalars(select(BillFact.id).order_by(BillFact.id)).all())

    def review_page(self, page: int, page_size: int, status: str = "") -> tuple[list[dict], int]:
        clauses = [
            ReviewCase.behavior_code != "DEFAULT",
            ReviewCaseBill.economic_id > 0,
        ]
        if status:
            clauses.append(ReviewCase.status == status)
        grouped = select(
            ReviewCase.id,
            ReviewCase.behavior_code,
            ReviewCase.status,
            ReviewCase.version,
            ReviewCase.title,
            ReviewCase.created_time,
            ReviewCase.updated_time,
            func.count(func.distinct(ReviewCaseBill.economic_id)).label("economic_count"),
            func.count(ReviewCaseBill.id).label("allocation_count"),
        ).join(
            ReviewCaseBill, ReviewCaseBill.case_id == ReviewCase.id,
        ).where(*clauses).group_by(ReviewCase.id)
        total = int(self.db.scalar(select(func.count()).select_from(grouped.subquery())) or 0)
        rows = self.db.execute(grouped.order_by(
            ReviewCase.updated_time.desc(), ReviewCase.id.desc(),
        ).offset((page - 1) * page_size).limit(page_size)).mappings().all()
        return [dict(row) for row in rows], total

    def fact_candidates(self, limit: int) -> list[dict]:
        rows = self.db.execute(select(
            BillFact.id,
            BillFact.occurred_time,
            BillFact.cash_direction,
            BillFact.amount_value,
            BillFact.amount_scale,
            BillFact.currency_code,
            BillFact.account_code,
            BillFact.counterparty,
            BillFact.summary,
            func.sum(ReviewCaseBill.amount_value).label("available_value"),
        ).join(
            ReviewCaseBill, ReviewCaseBill.bill_id == BillFact.id,
        ).join(
            ReviewCase, ReviewCase.id == ReviewCaseBill.case_id,
        ).join(
            LedgerEntry, LedgerEntry.id == ReviewCaseBill.economic_id,
        ).where(
            ReviewCase.behavior_code == "DEFAULT",
            ReviewCase.status == "CONFIRMED",
            LedgerEntry.status == "ACTIVE",
        ).group_by(BillFact.id).order_by(
            BillFact.occurred_time.desc(), BillFact.id.desc(),
        ).limit(limit)).mappings().all()
        return [dict(row) for row in rows]

    def idempotency(self, key: str):
        if not key:
            return None
        return self.db.execute(select(
            ReviewHistory.case_id,
            ReviewHistory.operation,
            ReviewHistory.request_json,
        ).where(ReviewHistory.idempotency_key == key)).mappings().one_or_none()

    def legacy_ledger_ids(self, fact_ids: list[int]) -> dict[int, int]:
        if not fact_ids:
            return {}
        rows = self.db.execute(select(
            LedgerEntrySource.source_id,
            LedgerEntrySource.ledger_id,
        ).where(
            LedgerEntrySource.source_kind == "BILL_FACT",
            LedgerEntrySource.source_id.in_(fact_ids),
        )).mappings().all()
        fact_count_by_ledger: dict[int, int] = defaultdict(int)
        for row in rows:
            fact_count_by_ledger[row["ledger_id"]] += 1
        return {
            row["source_id"]: row["ledger_id"]
            for row in rows
            if fact_count_by_ledger[row["ledger_id"]] == 1
        }

    def covered_fact_ids(self, fact_ids: list[int]) -> set[int]:
        if not fact_ids:
            return set()
        return set(self.db.scalars(select(ReviewCaseBill.bill_id).join(
            ReviewCase, ReviewCase.id == ReviewCaseBill.case_id,
        ).join(
            LedgerEntry, LedgerEntry.id == ReviewCaseBill.economic_id,
        ).where(
            ReviewCaseBill.bill_id.in_(fact_ids),
            ReviewCaseBill.economic_id > 0,
            ReviewCase.status == "CONFIRMED",
            LedgerEntry.status == "ACTIVE",
        ).distinct()).all())

    def create_default(
        self,
        fact: TargetReviewFactVO,
        amount_value: int,
        now: datetime,
        *,
        legacy_ledger_id: int = 0,
        operation: str = "AUTO_REVIEW",
    ) -> int:
        return self.create_defaults(
            [(fact, amount_value, legacy_ledger_id)], now, operation=operation
        )[0]

    def create_defaults(
        self,
        values: list[tuple[TargetReviewFactVO, int, int]],
        now: datetime,
        *,
        operation: str = "AUTO_REVIEW",
    ) -> list[int]:
        if not values:
            return []
        legacy_ids = [legacy_id for _fact, _amount, legacy_id in values if legacy_id]
        existing = {
            entry.id: entry
            for entry in self.db.scalars(select(LedgerEntry).where(
                LedgerEntry.id.in_(legacy_ids)
            )).all()
        } if legacy_ids else {}
        cases = [ReviewCase(
            review_type="DEFAULT",
            behavior_code="DEFAULT",
            status="CONFIRMED",
            allocation_status="COMPLETE",
            version=1,
            title=fact.counterparty or fact.summary or "默认交易",
            result_json="{}",
            created_time=now,
            updated_time=now,
        ) for fact, _amount, _legacy_id in values]
        self.db.add_all(cases)
        self.db.flush()
        entries = []
        new_entries = []
        for case, (fact, amount_value, legacy_id) in zip(cases, values):
            fields = self._economic_fields(
                economic_type="TRANSACTION",
                direction=fact.cash_direction,
                amount_value=amount_value,
                amount_scale=fact.amount_scale,
                currency_code=fact.currency_code,
                title=case.title,
                start_time=fact.occurred_time,
                end_time=fact.occurred_time,
                account_code=fact.account_code,
                claim_key="",
                claim_side="UNKNOWN",
                reversal_of_id=0,
                status="ACTIVE",
                input_hash=self._hash({
                    "default_review": case.id,
                    "fact_id": fact.id,
                    "amount_value": amount_value,
                }),
                legacy_type="INCOME" if fact.cash_direction == "IN" else "EXPENSE",
                now=now,
            )
            entry = existing.get(legacy_id)
            if entry is None:
                fields["allocation_status"] = "V2_ONLY"
                entry = LedgerEntry(created_time=now, projection_version=1, **fields)
                new_entries.append(entry)
            else:
                for name, value in fields.items():
                    setattr(entry, name, value)
                entry.projection_version += 1
            entries.append(entry)
        self.db.add_all(new_entries)
        self.db.flush()
        allocations = [ReviewCaseBill(
            case_id=case.id,
            bill_id=fact.id,
            economic_id=entry.id,
            role="DEFAULT_TRANSACTION",
            party="",
            amount_value=amount_value,
            amount_scale=fact.amount_scale,
            currency_code=fact.currency_code,
            created_time=now,
            updated_time=now,
        ) for case, entry, (fact, amount_value, _legacy_id) in zip(cases, entries, values)]
        self.db.add_all(allocations)
        self.db.flush()
        histories = []
        for case, entry, allocation, (fact, amount_value, _legacy_id) in zip(
            cases, entries, allocations, values
        ):
            request_json = self._canonical({
                "operation": operation,
                "fact_id": fact.id,
                "amount_value": amount_value,
            })
            after_json = self._snapshot(case, [entry], [allocation])
            histories.append(ReviewHistory(
                case_id=case.id,
                version=case.version,
                operation=operation,
                schema_version=2,
                request_json=request_json,
                before_json="{}",
                after_json=after_json,
                snapshot_hash=hashlib.sha256(after_json.encode()).hexdigest(),
                reverses_history_id=0,
                actor="system",
                reason="ensure every accepted fact has complete economic coverage",
                idempotency_key="",
                created_time=now,
                updated_time=now,
            ))
        self.db.add_all(histories)
        self.db.flush()
        return [case.id for case in cases]

    def create_draft(
        self,
        *,
        behavior_code: str,
        title: str,
        result_json: str,
        economics: list[dict],
        allocations: list[dict],
        request_json: str,
        actor: str,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> int:
        case = ReviewCase(
            review_type=behavior_code,
            behavior_code=behavior_code,
            status="PENDING",
            allocation_status="COMPLETE",
            version=1,
            title=title,
            result_json=result_json,
            created_time=now,
            updated_time=now,
        )
        self.db.add(case)
        self.db.flush()
        entries = []
        by_key = {}
        for item in economics:
            fields = self._economic_fields(
                **item,
                status="PENDING",
                input_hash=self._hash({"case_id": case.id, **item}),
                legacy_type=item["economic_type"],
                now=now,
            )
            fields["allocation_status"] = "V2_ONLY"
            entry = LedgerEntry(created_time=now, projection_version=1, **fields)
            self.db.add(entry)
            entries.append(entry)
            by_key[item["client_key"]] = entry
        self.db.flush()
        rows = []
        for item in allocations:
            row = ReviewCaseBill(
                case_id=case.id,
                bill_id=item["fact_id"],
                economic_id=by_key[item["economic_key"]].id,
                role=item["role"],
                party="",
                amount_value=item["amount_value"],
                amount_scale=item["amount_scale"],
                currency_code=item["currency_code"],
                created_time=now,
                updated_time=now,
            )
            self.db.add(row)
            rows.append(row)
        self.db.flush()
        snapshot = self._snapshot(case, entries, rows)
        self._add_history(
            case=case,
            operation="CREATE",
            request_json=request_json,
            before_json="{}",
            after_json=snapshot,
            reverses_history_id=0,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
            now=now,
        )
        return case.id

    def replace_draft(
        self,
        case_id: int,
        *,
        expected_version: int,
        behavior_code: str,
        title: str,
        result_json: str,
        economics: list[dict],
        allocations: list[dict],
        request_json: str,
        actor: str,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> bool:
        case = self.db.get(ReviewCase, case_id)
        if case is None or case.version != expected_version or case.status != "PENDING":
            return False
        before = self.detail(case_id)
        old_economic_ids = list(self.db.scalars(select(
            ReviewCaseBill.economic_id,
        ).where(
            ReviewCaseBill.case_id == case_id,
            ReviewCaseBill.economic_id > 0,
        ).distinct()).all())
        self.db.execute(delete(ReviewCaseBill).where(ReviewCaseBill.case_id == case_id))
        if old_economic_ids:
            self.db.execute(delete(LedgerEntry).where(LedgerEntry.id.in_(old_economic_ids)))
        case.review_type = behavior_code
        case.behavior_code = behavior_code
        case.title = title
        case.result_json = result_json
        case.version += 1
        case.updated_time = now
        entries = []
        by_key = {}
        for item in economics:
            fields = self._economic_fields(
                **item,
                status="PENDING",
                input_hash=self._hash({"case_id": case.id, "version": case.version, **item}),
                legacy_type=item["economic_type"],
                now=now,
            )
            fields["allocation_status"] = "V2_ONLY"
            entry = LedgerEntry(created_time=now, projection_version=1, **fields)
            self.db.add(entry)
            entries.append(entry)
            by_key[item["client_key"]] = entry
        self.db.flush()
        rows = [ReviewCaseBill(
            case_id=case.id,
            bill_id=item["fact_id"],
            economic_id=by_key[item["economic_key"]].id,
            role=item["role"],
            party="",
            amount_value=item["amount_value"],
            amount_scale=item["amount_scale"],
            currency_code=item["currency_code"],
            created_time=now,
            updated_time=now,
        ) for item in allocations]
        self.db.add_all(rows)
        self.db.flush()
        after = self.detail(case_id)
        self._add_history(
            case=case,
            operation="UPDATE",
            request_json=request_json,
            before_json=self._review_json(before),
            after_json=self._review_json(after),
            reverses_history_id=0,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
            now=now,
        )
        return True

    def default_allocations(self, fact_ids: list[int]) -> list[dict]:
        if not fact_ids:
            return []
        return list(self.db.execute(select(
            ReviewCaseBill.id,
            ReviewCaseBill.case_id,
            ReviewCaseBill.bill_id,
            ReviewCaseBill.economic_id,
            ReviewCaseBill.amount_value,
            ReviewCaseBill.amount_scale,
            ReviewCaseBill.currency_code,
            ReviewCase.version,
        ).join(
            ReviewCase, ReviewCase.id == ReviewCaseBill.case_id,
        ).join(
            LedgerEntry, LedgerEntry.id == ReviewCaseBill.economic_id,
        ).where(
            ReviewCaseBill.bill_id.in_(fact_ids),
            ReviewCase.behavior_code == "DEFAULT",
            ReviewCase.status == "CONFIRMED",
            LedgerEntry.status == "ACTIVE",
        ).order_by(ReviewCaseBill.bill_id, ReviewCaseBill.id)).mappings().all())

    def activate_case(
        self,
        case_id: int,
        expected_version: int,
        default_rows: list[dict],
        residuals: list[tuple[TargetReviewFactVO, int]],
        *,
        operation: str,
        request_json: str,
        actor: str,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> bool:
        case = self.db.get(ReviewCase, case_id)
        if case is None or case.version != expected_version:
            return False
        before = self.detail(case_id)
        default_case_ids = sorted({row["case_id"] for row in default_rows})
        default_economic_ids = sorted({row["economic_id"] for row in default_rows})
        if default_case_ids:
            defaults = list(self.db.scalars(select(ReviewCase).where(
                ReviewCase.id.in_(default_case_ids)
            )).all())
            rows_by_case = defaultdict(list)
            for row in default_rows:
                rows_by_case[row["case_id"]].append(dict(row))
            for default in defaults:
                before_default = self._canonical({
                    "id": default.id,
                    "behavior_code": "DEFAULT",
                    "status": "CONFIRMED",
                    "version": default.version,
                    "allocations": rows_by_case[default.id],
                })
                default.status = "REVOKED"
                default.version += 1
                default.updated_time = now
                after_default = self._canonical({
                    "id": default.id,
                    "behavior_code": "DEFAULT",
                    "status": "REVOKED",
                    "version": default.version,
                    "allocations": rows_by_case[default.id],
                })
                self.db.add(ReviewHistory(
                    case_id=default.id,
                    version=default.version,
                    operation="AUTO_REPLACED",
                    schema_version=2,
                    request_json=self._canonical({
                        "operation": "AUTO_REPLACED",
                        "replacement_case_id": case_id,
                    }),
                    before_json=before_default,
                    after_json=after_default,
                    snapshot_hash=hashlib.sha256(after_default.encode()).hexdigest(),
                    reverses_history_id=0,
                    actor="system",
                    reason="amount was reassigned by a confirmed economic review",
                    idempotency_key="",
                    created_time=now,
                    updated_time=now,
                ))
            entries = list(self.db.scalars(select(LedgerEntry).where(
                LedgerEntry.id.in_(default_economic_ids)
            )).all())
            for entry in entries:
                entry.status = "REVOKED"
                entry.updated_time = now
                entry.projection_version += 1
        case.status = "CONFIRMED"
        case.version += 1
        case.updated_time = now
        economics = list(self.db.scalars(select(LedgerEntry).join(
            ReviewCaseBill, ReviewCaseBill.economic_id == LedgerEntry.id,
        ).where(ReviewCaseBill.case_id == case_id).distinct()).all())
        for entry in economics:
            entry.status = "ACTIVE"
            entry.updated_time = now
            entry.projection_version += 1
        self.db.flush()
        self.create_defaults(
            [(fact, amount, 0) for fact, amount in residuals],
            now,
            operation="AUTO_RESIDUAL",
        )
        self.db.flush()
        after = self.detail(case_id)
        self._add_history(
            case=case,
            operation=operation,
            request_json=request_json,
            before_json=self._review_json(before),
            after_json=self._review_json(after),
            reverses_history_id=0,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
            now=now,
        )
        return True

    def revoke_case(
        self,
        case_id: int,
        expected_version: int,
        facts_by_id: dict[int, TargetReviewFactVO],
        released: dict[int, int],
        *,
        request_json: str,
        actor: str,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> bool:
        case = self.db.get(ReviewCase, case_id)
        if case is None or case.version != expected_version:
            return False
        before = self.detail(case_id)
        economic_ids = list(self.db.scalars(select(
            ReviewCaseBill.economic_id,
        ).where(ReviewCaseBill.case_id == case_id).distinct()).all())
        case.status = "REVOKED"
        case.version += 1
        case.updated_time = now
        if economic_ids:
            entries = list(self.db.scalars(select(LedgerEntry).where(
                LedgerEntry.id.in_(economic_ids)
            )).all())
            for entry in entries:
                entry.status = "REVOKED"
                entry.updated_time = now
                entry.projection_version += 1
        self.db.flush()
        self.create_defaults([
            (facts_by_id[fact_id], amount, 0)
            for fact_id, amount in sorted(released.items())
        ], now, operation="AUTO_RESTORE")
        self.db.flush()
        after = self.detail(case_id)
        self._add_history(
            case=case,
            operation="REVOKE",
            request_json=request_json,
            before_json=self._review_json(before),
            after_json=self._review_json(after),
            reverses_history_id=0,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
            now=now,
        )
        return True

    def fact_coverage(self, fact_ids: list[int]) -> dict[int, int]:
        if not fact_ids:
            return {}
        rows = self.db.execute(select(
            ReviewCaseBill.bill_id,
            func.sum(ReviewCaseBill.amount_value).label("amount_value"),
        ).join(
            ReviewCase, ReviewCase.id == ReviewCaseBill.case_id,
        ).join(
            LedgerEntry, LedgerEntry.id == ReviewCaseBill.economic_id,
        ).where(
            ReviewCaseBill.bill_id.in_(fact_ids),
            ReviewCase.status == "CONFIRMED",
            LedgerEntry.status == "ACTIVE",
            ReviewCaseBill.economic_id > 0,
        ).group_by(ReviewCaseBill.bill_id)).mappings().all()
        return {row["bill_id"]: int(row["amount_value"] or 0) for row in rows}

    def economic_flows(self, economic_ids: list[int]) -> dict[int, dict]:
        if not economic_ids:
            return {}
        rows = self.db.execute(select(
            LedgerEntry.id,
            LedgerEntry.economic_type,
            LedgerEntry.cash_direction,
            LedgerEntry.amount_value,
            LedgerEntry.amount_scale,
            LedgerEntry.currency_code,
            LedgerEntry.reversal_of_id,
            LedgerEntry.status,
        ).where(LedgerEntry.id.in_(economic_ids))).mappings().all()
        return {row["id"]: dict(row) for row in rows}

    def active_economic_facts(self, fact_ids: list[int]) -> dict[int, list[int]]:
        if not fact_ids:
            return {}
        rows = self.db.execute(select(
            ReviewCaseBill.economic_id,
            ReviewCaseBill.bill_id,
        ).join(
            ReviewCase, ReviewCase.id == ReviewCaseBill.case_id,
        ).join(
            LedgerEntry, LedgerEntry.id == ReviewCaseBill.economic_id,
        ).where(
            ReviewCaseBill.bill_id.in_(fact_ids),
            ReviewCase.status == "CONFIRMED",
            LedgerEntry.status == "ACTIVE",
            ReviewCaseBill.economic_id > 0,
        ).distinct().order_by(
            ReviewCaseBill.economic_id, ReviewCaseBill.bill_id,
        )).mappings().all()
        result: dict[int, list[int]] = defaultdict(list)
        for row in rows:
            result[row["economic_id"]].append(row["bill_id"])
        return dict(result)

    def reversal_usage(
        self,
        economic_ids: list[int],
        *,
        exclude_ids: list[int] | None = None,
    ) -> dict[int, tuple[int, int]]:
        if not economic_ids:
            return {}
        clauses = [
            LedgerEntry.reversal_of_id.in_(economic_ids),
            LedgerEntry.status == "ACTIVE",
        ]
        if exclude_ids:
            clauses.append(~LedgerEntry.id.in_(exclude_ids))
        rows = self.db.execute(select(
            LedgerEntry.reversal_of_id,
            LedgerEntry.amount_scale,
            func.sum(LedgerEntry.amount_value).label("amount_value"),
        ).where(*clauses).group_by(
            LedgerEntry.reversal_of_id,
            LedgerEntry.amount_scale,
        )).mappings().all()
        result: dict[int, tuple[int, int]] = {}
        for row in rows:
            target_id = row["reversal_of_id"]
            scale = row["amount_scale"]
            value = int(row["amount_value"] or 0)
            previous = result.get(target_id)
            if previous is None:
                result[target_id] = (value, scale)
            else:
                common = max(previous[1], scale)
                result[target_id] = (
                    previous[0] * 10 ** (common - previous[1])
                    + value * 10 ** (common - scale),
                    common,
                )
        return result

    def detail(self, case_id: int) -> TargetEconomicReviewRead | None:
        case = self.db.execute(select(
            ReviewCase.id,
            ReviewCase.behavior_code,
            ReviewCase.status,
            ReviewCase.version,
            ReviewCase.title,
            ReviewCase.result_json,
            ReviewCase.created_time,
            ReviewCase.updated_time,
        ).where(ReviewCase.id == case_id)).mappings().one_or_none()
        if case is None:
            return None
        allocation_rows = self.db.execute(select(
            ReviewCaseBill.id,
            ReviewCaseBill.bill_id.label("fact_id"),
            ReviewCaseBill.economic_id,
            ReviewCaseBill.amount_value,
            ReviewCaseBill.amount_scale,
            ReviewCaseBill.currency_code,
            ReviewCaseBill.role,
        ).where(
            ReviewCaseBill.case_id == case_id,
            ReviewCaseBill.economic_id > 0,
        ).order_by(ReviewCaseBill.id)).mappings().all()
        economic_ids = sorted({row["economic_id"] for row in allocation_rows})
        economic_rows = self.db.execute(select(
            LedgerEntry.id,
            LedgerEntry.economic_type,
            LedgerEntry.cash_direction,
            LedgerEntry.amount_value,
            LedgerEntry.amount_scale,
            LedgerEntry.currency_code,
            LedgerEntry.title,
            LedgerEntry.start_time,
            LedgerEntry.end_time,
            LedgerEntry.claim_key,
            LedgerEntry.claim_side,
            LedgerEntry.reversal_of_id,
            LedgerEntry.status,
            LedgerEntry.projection_version,
        ).where(LedgerEntry.id.in_(economic_ids)).order_by(LedgerEntry.id)).mappings().all() if economic_ids else []
        history_rows = self.db.execute(select(
            ReviewHistory.id,
            ReviewHistory.version,
            ReviewHistory.operation,
            ReviewHistory.schema_version,
            ReviewHistory.request_json,
            ReviewHistory.before_json,
            ReviewHistory.after_json,
            ReviewHistory.snapshot_hash,
            ReviewHistory.reverses_history_id,
            ReviewHistory.actor,
            ReviewHistory.reason,
            ReviewHistory.idempotency_key,
            ReviewHistory.created_time,
        ).where(ReviewHistory.case_id == case_id).order_by(
            ReviewHistory.version, ReviewHistory.id,
        )).mappings().all()
        return TargetEconomicReviewRead(
            id=case["id"],
            behavior_code=case["behavior_code"],
            status=case["status"],
            version=case["version"],
            title=case["title"],
            result=json.loads(case["result_json"]),
            economics=[TargetEconomicFlowRead(**row) for row in economic_rows],
            allocations=[TargetFlowAllocationRead(**row) for row in allocation_rows],
            history=[TargetReviewHistoryRead(
                id=row["id"],
                version=row["version"],
                operation=row["operation"],
                schema_version=row["schema_version"],
                request=json.loads(row["request_json"]),
                before=json.loads(row["before_json"]),
                after=json.loads(row["after_json"]),
                snapshot_hash=row["snapshot_hash"],
                reverses_history_id=row["reverses_history_id"],
                actor=row["actor"],
                reason=row["reason"],
                idempotency_key=row["idempotency_key"],
                created_time=row["created_time"],
            ) for row in history_rows],
            created_time=case["created_time"],
            updated_time=case["updated_time"],
        )

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()

    @staticmethod
    def _economic_fields(
        *,
        economic_type: str,
        direction: str,
        amount_value: int,
        amount_scale: int,
        currency_code: str,
        title: str,
        start_time: datetime,
        end_time: datetime,
        account_code: str,
        claim_key: str,
        claim_side: str,
        reversal_of_id: int,
        status: str,
        input_hash: str,
        legacy_type: str,
        now: datetime,
        client_key: str = "",
    ) -> dict:
        incoming = amount_value if direction == "IN" else 0
        outgoing = amount_value if direction == "OUT" else 0
        return {
            "ledger_type": legacy_type,
            "entry_type": {
                "TRANSACTION": 0,
                "ACCOUNT_TRANSFER": 1,
                "CLAIM": 2,
            }[economic_type],
            "entry_direction": 1 if direction == "IN" else 2,
            "account_code": account_code,
            "counterparty_account_ref": "",
            "occurred_time": start_time,
            "economic_type": economic_type,
            "cash_direction": direction,
            "amount_value": amount_value,
            "amount_scale": amount_scale,
            "currency_code": currency_code,
            "claim_key": claim_key,
            "claim_side": claim_side,
            "reversal_of_id": reversal_of_id,
            "status": status,
            "allocation_status": "COMPLETE",
            "title": title,
            "start_time": start_time,
            "end_time": end_time,
            "in_amount_value": incoming,
            "in_amount_scale": amount_scale,
            "in_currency_code": currency_code,
            "out_amount_value": outgoing,
            "out_amount_scale": amount_scale,
            "out_currency_code": currency_code,
            "in_account_code": account_code if incoming else "UNKNOWN",
            "out_account_code": account_code if outgoing else "UNKNOWN",
            "input_hash": input_hash,
            "updated_time": now,
        }

    def _add_history(
        self,
        *,
        case: ReviewCase,
        operation: str,
        request_json: str,
        before_json: str,
        after_json: str,
        reverses_history_id: int,
        actor: str,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> None:
        self.db.add(ReviewHistory(
            case_id=case.id,
            version=case.version,
            operation=operation,
            schema_version=2,
            request_json=request_json,
            before_json=before_json,
            after_json=after_json,
            snapshot_hash=hashlib.sha256(after_json.encode()).hexdigest(),
            reverses_history_id=reverses_history_id,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
            created_time=now,
            updated_time=now,
        ))
        self.db.flush()

    def _snapshot(self, case, entries, allocations) -> str:
        return self._canonical({
            "id": case.id,
            "behavior_code": case.behavior_code,
            "status": case.status,
            "version": case.version,
            "title": case.title,
            "economics": [{
                "id": entry.id,
                "economic_type": entry.economic_type,
                "cash_direction": entry.cash_direction,
                "amount_value": entry.amount_value,
                "amount_scale": entry.amount_scale,
                "currency_code": entry.currency_code,
            } for entry in entries],
            "allocations": [{
                "id": row.id,
                "fact_id": row.bill_id,
                "economic_id": row.economic_id,
                "amount_value": row.amount_value,
                "amount_scale": row.amount_scale,
                "currency_code": row.currency_code,
            } for row in allocations],
        })

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    @classmethod
    def _review_json(cls, value: TargetEconomicReviewRead | None) -> str:
        if value is None:
            return "{}"
        return cls._canonical(value.model_dump(
            mode="json",
            exclude={"history", "created_time", "updated_time"},
        ))

    @classmethod
    def _hash(cls, value: object) -> str:
        return hashlib.sha256(cls._canonical(value).encode()).hexdigest()
