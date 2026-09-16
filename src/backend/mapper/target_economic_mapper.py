from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy import String, cast, delete, func, or_, select, text
from sqlalchemy.orm import Session

from backend.entity import (
    BillFact,
    LedgerEntry,
    LedgerEntryTag,
    ReviewCase,
    ReviewCaseBill,
    ReviewHistory,
)
from backend.schema.target_review import (
    TargetEconomicFlowRead,
    TargetEconomicReviewRead,
    TargetEconomicReviewFilter,
    TargetEconomicReviewFactRead,
    TargetEconomicReviewSorter,
    TargetFlowAllocationRead,
    TargetReviewFactVO,
    TargetReviewHistoryRead,
)


ECONOMIC_TYPES = {0: "TRANSACTION", 1: "ACCOUNT_TRANSFER", 2: "CLAIM"}
ENTRY_TYPE_IDS = {value: key for key, value in ECONOMIC_TYPES.items()}
CASH_DIRECTIONS = {1: "IN", 2: "OUT"}


class TargetEconomicMapper:
    """Set-oriented persistence for production Review/Economic/Allocation commands."""

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

    def allocations_by_relation(
        self,
        *,
        review_ids: list[int] | None = None,
        fact_ids: list[int] | None = None,
        economic_ids: list[int] | None = None,
    ) -> list[dict]:
        """Read ternary Allocations through any supplied member identifiers."""

        clauses = []
        if review_ids:
            clauses.append(ReviewCaseBill.case_id.in_(review_ids))
        if fact_ids:
            clauses.append(ReviewCaseBill.bill_id.in_(fact_ids))
        if economic_ids:
            clauses.append(ReviewCaseBill.economic_id.in_(economic_ids))
        if not clauses:
            return []
        rows = self.db.execute(select(
            ReviewCaseBill.id,
            ReviewCaseBill.case_id.label("review_id"),
            ReviewCaseBill.bill_id.label("fact_id"),
            ReviewCaseBill.economic_id,
            ReviewCaseBill.amount_value,
            ReviewCaseBill.amount_scale,
            ReviewCaseBill.currency_code,
        ).where(*clauses).order_by(ReviewCaseBill.id)).mappings().all()
        return [dict(row) for row in rows]

    def review_page(
        self,
        page: int,
        page_size: int,
        q: str,
        filter_value: TargetEconomicReviewFilter,
        sorter: TargetEconomicReviewSorter,
    ) -> tuple[list[dict], int]:
        clauses = []
        if q:
            pattern = f"%{q}%"
            clauses.append(or_(
                cast(ReviewCase.id, String).like(pattern),
                ReviewCase.title.like(pattern),
                ReviewCase.behavior_code.like(pattern),
            ))
        if filter_value.status:
            clauses.append(ReviewCase.status == filter_value.status)
        if filter_value.behavior_code:
            clauses.append(ReviewCase.behavior_code == filter_value.behavior_code)
        if filter_value.exclude_behavior_code:
            clauses.append(ReviewCase.behavior_code != filter_value.exclude_behavior_code)
        grouped = select(
            ReviewCase.id,
            ReviewCase.behavior_code,
            ReviewCase.status,
            ReviewCase.version,
            ReviewCase.title,
            ReviewCase.created_time,
            ReviewCase.updated_time,
            func.count(ReviewCaseBill.id).label("economic_count"),
            func.count(ReviewCaseBill.id).label("allocation_count"),
        ).join(
            ReviewCaseBill, ReviewCaseBill.case_id == ReviewCase.id,
        ).where(*clauses).group_by(ReviewCase.id)
        total = int(self.db.scalar(select(func.count()).select_from(grouped.subquery())) or 0)
        sort_columns = {
            "id": ReviewCase.id,
            "created_time": ReviewCase.created_time,
            "updated_time": ReviewCase.updated_time,
            "version": ReviewCase.version,
        }
        column = sort_columns[sorter.field]
        order = column.asc() if sorter.order == "asc" else column.desc()
        id_order = ReviewCase.id.asc() if sorter.order == "asc" else ReviewCase.id.desc()
        rows = self.db.execute(grouped.order_by(
            order, id_order,
        ).offset((page - 1) * page_size).limit(page_size)).mappings().all()
        return [dict(row) for row in rows], total

    def _fact_candidate_query(self):
        return select(
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
        ).group_by(BillFact.id).order_by(
            BillFact.occurred_time.desc(), BillFact.id.desc(),
        )

    def fact_candidate_page(
        self,
        page: int,
        page_size: int,
    ) -> tuple[list[dict], int]:
        query = self._fact_candidate_query()
        total = self.db.scalar(select(func.count()).select_from(
            query.order_by(None).subquery()
        )) or 0
        rows = self.db.execute(query.offset(
            (page - 1) * page_size
        ).limit(page_size)).mappings().all()
        return [dict(row) for row in rows], total

    def fact_candidates(self, limit: int) -> list[dict]:
        rows = self.db.execute(
            self._fact_candidate_query().limit(limit)
        ).mappings().all()
        return [dict(row) for row in rows]

    def idempotency(self, key: str):
        if not key:
            return None
        return self.db.execute(select(
            ReviewHistory.case_id,
            ReviewHistory.operation,
            ReviewHistory.request_json,
        ).where(ReviewHistory.idempotency_key == key)).mappings().one_or_none()

    def latest_plan_payload(self, case_id: int) -> dict | None:
        value = self.db.scalar(select(ReviewHistory.request_json).where(
            ReviewHistory.case_id == case_id,
            ReviewHistory.operation.in_(("CREATE", "UPDATE")),
        ).order_by(ReviewHistory.version.desc(), ReviewHistory.id.desc()).limit(1))
        if value is None:
            return None
        payload = json.loads(value)
        for name in ("operation", "case_id", "expected_version"):
            payload.pop(name, None)
        return payload

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
        ).distinct()).all())

    def create_default(
        self,
        fact: TargetReviewFactVO,
        amount_value: int,
        account_code: str,
        now: datetime,
        *,
        operation: str = "AUTO_REVIEW",
    ) -> int:
        return self.create_defaults(
            [(fact, amount_value, account_code)],
            now,
            operation=operation,
        )[0]

    def create_defaults(
        self,
        values: list[tuple[TargetReviewFactVO, int, str]],
        now: datetime,
        *,
        operation: str = "AUTO_REVIEW",
    ) -> list[int]:
        if not values:
            return []
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
        ) for fact, _amount, _account_code in values]
        self.db.add_all(cases)
        self.db.flush()
        entries = []
        for case, (fact, amount_value, account_code) in zip(cases, values):
            fields = self._economic_fields(
                entry_type=0,
                direction=fact.cash_direction,
                amount_value=amount_value,
                amount_scale=fact.amount_scale,
                currency_code=fact.currency_code,
                account_code=account_code,
                occurred_time=fact.occurred_time,
                now=now,
            )
            entry = LedgerEntry(created_time=now, **fields)
            entries.append(entry)
        self.db.add_all(entries)
        self.db.flush()
        allocations = [ReviewCaseBill(
            case_id=case.id,
            bill_id=fact.id,
            economic_id=entry.id,
            entry_type=0,
            role="DEFAULT_TRANSACTION",
            party="",
            amount_value=amount_value,
            amount_scale=fact.amount_scale,
            currency_code=fact.currency_code,
            created_time=now,
            updated_time=now,
        ) for case, entry, (fact, amount_value, _account_code) in zip(cases, entries, values)]
        self.db.add_all(allocations)
        self.db.flush()
        histories = []
        for case, entry, allocation, (fact, amount_value, _account_code) in zip(
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
        entry_types = {
            item["client_key"]: item["entry_type"]
            for item in economics
        }
        rows = [ReviewCaseBill(
                case_id=case.id,
                bill_id=item["fact_id"],
                economic_id=0,
                entry_type=entry_types[item["economic_key"]],
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
        snapshot = self._review_json(self.detail(case.id))
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
        self.db.execute(delete(ReviewCaseBill).where(ReviewCaseBill.case_id == case_id))
        case.review_type = behavior_code
        case.behavior_code = behavior_code
        case.title = title
        case.result_json = result_json
        case.version += 1
        case.updated_time = now
        entry_types = {
            item["client_key"]: item["entry_type"]
            for item in economics
        }
        rows = [ReviewCaseBill(
            case_id=case.id,
            bill_id=item["fact_id"],
            economic_id=0,
            entry_type=entry_types[item["economic_key"]],
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
        ).order_by(ReviewCaseBill.bill_id, ReviewCaseBill.id)).mappings().all())

    def activate_case(
        self,
        case_id: int,
        expected_version: int,
        default_rows: list[dict],
        residuals: list[tuple[TargetReviewFactVO, int, str]],
        facts_by_id: dict[int, TargetReviewFactVO],
        economics: list[dict],
        allocations: list[dict],
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
            default_allocations = list(self.db.scalars(select(
                ReviewCaseBill
            ).where(ReviewCaseBill.id.in_([row["id"] for row in default_rows]))).all())
            for allocation in default_allocations:
                allocation.economic_id = 0
                allocation.updated_time = now
            self._delete_economics(default_economic_ids)
        case.status = "CONFIRMED"
        case.version += 1
        case.updated_time = now
        allocation_models = list(self.db.scalars(select(
            ReviewCaseBill
        ).where(ReviewCaseBill.case_id == case_id).order_by(ReviewCaseBill.id)).all())
        if len(allocation_models) != len(allocations):
            raise RuntimeError("review allocation plan changed before confirmation")
        economics_by_key = {item["client_key"]: item for item in economics}
        entries = []
        for allocation_model, allocation in zip(allocation_models, allocations):
            if (
                allocation_model.bill_id not in facts_by_id
                or
                allocation_model.bill_id != allocation["fact_id"]
                or allocation_model.amount_value != allocation["amount_value"]
            ):
                raise RuntimeError("review allocation plan changed before confirmation")
            item = economics_by_key[allocation["economic_key"]]
            fields = self._economic_fields(
                **item,
                now=now,
            )
            entry = LedgerEntry(created_time=now, **fields)
            self.db.add(entry)
            entries.append((allocation_model, entry))
        self.db.flush()
        for allocation_model, entry in entries:
            allocation_model.economic_id = entry.id
            allocation_model.updated_time = now
        self.db.flush()
        self.create_defaults(
            [(fact, amount, account_code) for fact, amount, account_code in residuals],
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
        account_codes: dict[int, str],
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
        allocations = list(self.db.scalars(select(ReviewCaseBill).where(
            ReviewCaseBill.case_id == case_id,
        )).all())
        economic_ids = sorted({
            row.economic_id for row in allocations if row.economic_id > 0
        })
        case.status = "REVOKED"
        case.version += 1
        case.updated_time = now
        for allocation in allocations:
            allocation.economic_id = 0
            allocation.updated_time = now
        self._delete_economics(economic_ids)
        self.db.flush()
        self.create_defaults([
            (facts_by_id[fact_id], amount, account_codes[fact_id])
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
            ReviewCaseBill.economic_id > 0,
        ).group_by(ReviewCaseBill.bill_id)).mappings().all()
        return {row["bill_id"]: int(row["amount_value"] or 0) for row in rows}

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
            ReviewCaseBill.economic_id > 0,
        ).distinct().order_by(
            ReviewCaseBill.economic_id, ReviewCaseBill.bill_id,
        )).mappings().all()
        result: dict[int, list[int]] = defaultdict(list)
        for row in rows:
            result[row["economic_id"]].append(row["bill_id"])
        return dict(result)

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
            ReviewCaseBill.entry_type,
            ReviewCaseBill.amount_value,
            ReviewCaseBill.amount_scale,
            ReviewCaseBill.currency_code,
            ReviewCaseBill.role,
        ).where(
            ReviewCaseBill.case_id == case_id,
        ).order_by(ReviewCaseBill.id)).mappings().all()
        economic_ids = sorted({
            row["economic_id"] for row in allocation_rows if row["economic_id"] > 0
        })
        economic_rows = self.db.execute(select(
            LedgerEntry.id,
            LedgerEntry.entry_type,
            LedgerEntry.entry_direction,
            LedgerEntry.amount_value,
            LedgerEntry.amount_scale,
            LedgerEntry.currency_code,
            LedgerEntry.account_code,
            LedgerEntry.counterparty_account_ref,
            LedgerEntry.occurred_time,
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
        economics_by_id = {row["id"]: dict(row) for row in economic_rows}
        fact_ids = sorted({row["fact_id"] for row in allocation_rows})
        fact_rows = self.db.execute(select(
            BillFact.id,
            BillFact.occurred_time,
            BillFact.cash_direction,
            BillFact.amount_value,
            BillFact.amount_scale,
            BillFact.currency_code,
            BillFact.account_code,
            BillFact.counterparty,
            BillFact.summary,
        ).where(BillFact.id.in_(fact_ids)).order_by(BillFact.id)).mappings().all() if fact_ids else []
        facts_by_id = {row["id"]: row for row in fact_rows}
        plan = next((
            json.loads(row["request_json"])
            for row in reversed(history_rows)
            if row["operation"] in {"CREATE", "UPDATE"}
        ), {})
        definitions = {
            item["client_key"]: item
            for item in plan.get("economics", plan.get("entries", []))
        }
        planned_allocations = plan.get("allocations", [])
        materialized_economics = []
        for index, allocation in enumerate(allocation_rows):
            if allocation["economic_id"] > 0:
                materialized_economics.append(
                    economics_by_id[allocation["economic_id"]]
                )
                continue
            fact = facts_by_id[allocation["fact_id"]]
            planned = (
                planned_allocations[index]
                if index < len(planned_allocations)
                else {}
            )
            economic_key = planned.get("economic_key", planned.get("entry_key", ""))
            definition = definitions.get(economic_key, {})
            entry_type = definition.get("entry_type")
            if entry_type is None:
                entry_type = ENTRY_TYPE_IDS.get(
                    definition.get("economic_type"),
                    allocation["entry_type"],
                )
            materialized_economics.append({
                "id": 0,
                "entry_type": entry_type,
                "entry_direction": 1 if fact["cash_direction"] == "IN" else 2,
                "amount_value": allocation["amount_value"],
                "amount_scale": allocation["amount_scale"],
                "currency_code": allocation["currency_code"],
                "account_code": fact["account_code"],
                "counterparty_account_ref": "",
                "occurred_time": fact["occurred_time"],
            })
        return TargetEconomicReviewRead(
            id=case["id"],
            behavior_code=case["behavior_code"],
            status=case["status"],
            version=case["version"],
            title=case["title"],
            result=json.loads(case["result_json"]),
            facts=[TargetEconomicReviewFactRead(**row) for row in fact_rows],
            economics=[TargetEconomicFlowRead(
                id=row["id"],
                economic_type=ECONOMIC_TYPES[row["entry_type"]],
                cash_direction=CASH_DIRECTIONS[row["entry_direction"]],
                amount_value=row["amount_value"],
                amount_scale=row["amount_scale"],
                currency_code=row["currency_code"],
                account_code=row["account_code"],
                counterparty_account_ref=row["counterparty_account_ref"],
                occurred_time=row["occurred_time"],
            ) for row in materialized_economics],
            allocations=[TargetFlowAllocationRead(**{
                name: value
                for name, value in row.items()
                if name not in {"entry_type", "role"}
            }) for row in allocation_rows],
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

    def _delete_economics(self, economic_ids: list[int]) -> None:
        if not economic_ids:
            return
        self.db.execute(delete(LedgerEntryTag).where(
            LedgerEntryTag.ledger_id.in_(economic_ids)
        ))
        self.db.execute(delete(LedgerEntry).where(
            LedgerEntry.id.in_(economic_ids)
        ))

    @staticmethod
    def _economic_fields(
        *,
        entry_type: int,
        direction: str,
        amount_value: int,
        amount_scale: int,
        currency_code: str,
        account_code: str,
        occurred_time: datetime,
        now: datetime,
        client_key: str = "",
    ) -> dict:
        return {
            "entry_type": entry_type,
            "entry_direction": 1 if direction == "IN" else 2,
            "account_code": account_code,
            "counterparty_account_ref": "",
            "occurred_time": occurred_time,
            "amount_value": amount_value,
            "amount_scale": amount_scale,
            "currency_code": currency_code,
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
                "entry_type": entry.entry_type,
                "entry_direction": entry.entry_direction,
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
