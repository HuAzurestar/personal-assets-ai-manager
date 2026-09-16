from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy import String, cast, delete, exists, func, or_, select, text
from sqlalchemy.orm import Session

from backend.entity import (
    CASH_DIRECTION_IN,
    CASH_DIRECTION_OUT,
    LedgerEntry,
    LedgerEntryTag,
    ReviewAllocation,
    ReviewCase,
    ReviewRevision,
    TransactionFact,
)
from backend.schema.target_review import (
    TargetEconomicFlowRead,
    TargetEconomicReviewFactRead,
    TargetEconomicReviewRead,
    TargetFlowAllocationRead,
    TargetReviewFactVO,
    TargetReviewHistoryRead,
)


ECONOMIC_TYPES = {0: "TRANSACTION", 1: "ACCOUNT_TRANSFER", 2: "CLAIM"}
ECONOMIC_TYPE_IDS = {value: key for key, value in ECONOMIC_TYPES.items()}
CASH_DIRECTIONS = {CASH_DIRECTION_IN: "IN", CASH_DIRECTION_OUT: "OUT"}
REVISION_OPERATIONS = {0: "CREATE", 1: "UPDATE", 2: "REVOKE", 3: "RESTORE"}


class TargetEconomicMapper:
    """Persistence adapter from the semantic Router contract to the DB schema."""

    def __init__(self, db: Session):
        self.db = db

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def all_fact_ids(self) -> list[int]:
        return list(self.db.scalars(
            select(TransactionFact.id).order_by(TransactionFact.id)
        ).all())

    def facts(self, fact_ids: list[int]) -> tuple[TargetReviewFactVO, ...]:
        if not fact_ids:
            return ()
        rows = self.db.execute(select(
            TransactionFact.id,
            TransactionFact.occurred_time,
            TransactionFact.cash_direction,
            TransactionFact.amount_value,
            TransactionFact.amount_scale,
            TransactionFact.currency_code,
            TransactionFact.account_code,
            TransactionFact.counterparty_name.label("counterparty"),
            TransactionFact.summary,
            TransactionFact.fact_key,
            TransactionFact.created_time,
            TransactionFact.updated_time,
        ).where(TransactionFact.id.in_(fact_ids))).mappings().all()
        return tuple(TargetReviewFactVO(**self._fact_values(row)) for row in rows)

    def allocations_by_relation(
        self,
        *,
        review_ids: list[int] | None = None,
        fact_ids: list[int] | None = None,
        economic_ids: list[int] | None = None,
    ) -> list[dict]:
        clauses = []
        if review_ids:
            clauses.append(ReviewAllocation.review_case_id.in_(review_ids))
        if fact_ids:
            clauses.append(ReviewAllocation.transaction_fact_id.in_(fact_ids))
        if economic_ids:
            clauses.append(ReviewAllocation.ledger_entry_id.in_(economic_ids))
        if not clauses:
            return []
        rows = self.db.execute(select(
            ReviewAllocation.id,
            ReviewAllocation.review_case_id.label("review_id"),
            ReviewAllocation.transaction_fact_id.label("fact_id"),
            ReviewAllocation.ledger_entry_id.label("economic_id"),
            ReviewAllocation.amount_value,
            ReviewAllocation.amount_scale,
            ReviewAllocation.currency_code,
        ).where(*clauses).order_by(ReviewAllocation.id)).mappings().all()
        return [dict(row) for row in rows]

    def review_page(
        self,
        page: int,
        page_size: int,
        q: str,
        filter_value,
        sorter,
    ) -> tuple[list[dict], int]:
        grouped = select(
            ReviewCase.id,
            ReviewCase.behavior_type,
            ReviewCase.status,
            ReviewCase.title,
            ReviewCase.created_time,
            ReviewCase.updated_time,
            func.count(func.distinct(ReviewAllocation.ledger_entry_id)).label(
                "allocation_count"
            ),
            func.count(func.distinct(ReviewRevision.id)).label("version"),
        ).outerjoin(
            ReviewAllocation,
            ReviewAllocation.review_case_id == ReviewCase.id,
        ).outerjoin(
            ReviewRevision,
            ReviewRevision.review_case_id == ReviewCase.id,
        ).group_by(ReviewCase.id)
        rows = [dict(row) for row in self.db.execute(grouped).mappings().all()]
        request_by_case = self._latest_plan_requests([row["id"] for row in rows])
        items = []
        for row in rows:
            request = request_by_case.get(row["id"], {})
            item = {
                **row,
                "behavior_code": request.get(
                    "behavior_code", self._behavior_code(row["behavior_type"])
                ),
                "status": self._status_name(row["status"], row["allocation_count"]),
                "economic_count": row["allocation_count"],
                "version": max(1, int(row["version"])),
            }
            if q:
                needle = q.casefold()
                if (
                    needle not in str(item["id"])
                    and needle not in item["title"].casefold()
                    and needle not in item["behavior_code"].casefold()
                ):
                    continue
            if filter_value.status and item["status"] != filter_value.status:
                continue
            if (
                filter_value.behavior_code
                and item["behavior_code"] != filter_value.behavior_code
            ):
                continue
            if (
                filter_value.exclude_behavior_code
                and item["behavior_code"] == filter_value.exclude_behavior_code
            ):
                continue
            items.append(item)
        reverse = sorter.order == "desc"
        items.sort(key=lambda item: (item[sorter.field], item["id"]), reverse=reverse)
        total = len(items)
        offset = (page - 1) * page_size
        return items[offset:offset + page_size], total

    def _fact_candidate_query(self, q: str = "", filter_value=None):
        clauses = [ReviewCase.status == 0, self._system_case_exists()]
        if q:
            pattern = f"%{q}%"
            clauses.append(or_(
                cast(TransactionFact.id, String).like(pattern),
                TransactionFact.account_code.ilike(pattern),
                TransactionFact.counterparty_name.ilike(pattern),
                TransactionFact.summary.ilike(pattern),
            ))
        if filter_value is not None:
            if filter_value.cash_direction:
                clauses.append(TransactionFact.cash_direction == {
                    "IN": CASH_DIRECTION_IN,
                    "OUT": CASH_DIRECTION_OUT,
                }[filter_value.cash_direction])
            if filter_value.currency_code:
                clauses.append(
                    TransactionFact.currency_code == filter_value.currency_code.upper()
                )
            if filter_value.account_code:
                clauses.append(TransactionFact.account_code == filter_value.account_code)
        return select(
            TransactionFact.id,
            TransactionFact.occurred_time,
            TransactionFact.cash_direction,
            TransactionFact.amount_value,
            TransactionFact.amount_scale,
            TransactionFact.currency_code,
            TransactionFact.account_code,
            TransactionFact.counterparty_name.label("counterparty"),
            TransactionFact.summary,
            func.sum(ReviewAllocation.amount_value).label("available_value"),
        ).join(
            ReviewAllocation,
            ReviewAllocation.transaction_fact_id == TransactionFact.id,
        ).join(
            ReviewCase,
            ReviewCase.id == ReviewAllocation.review_case_id,
        ).join(
            LedgerEntry,
            LedgerEntry.id == ReviewAllocation.ledger_entry_id,
        ).where(*clauses).group_by(TransactionFact.id)

    def fact_candidate_page(
        self,
        page: int,
        page_size: int,
        q: str = "",
        filter_value=None,
        sorter=None,
    ) -> tuple[list[dict], int]:
        query = self._fact_candidate_query(q, filter_value)
        total = int(self.db.scalar(
            select(func.count()).select_from(query.order_by(None).subquery())
        ) or 0)
        sort_field = sorter.field if sorter is not None else "occurred_time"
        sort_order = sorter.order if sorter is not None else "desc"
        columns = {
            "id": TransactionFact.id,
            "occurred_time": TransactionFact.occurred_time,
            "amount_value": TransactionFact.amount_value,
            "available_value": func.sum(ReviewAllocation.amount_value),
        }
        column = columns[sort_field]
        order = column.asc() if sort_order == "asc" else column.desc()
        id_order = (
            TransactionFact.id.asc() if sort_order == "asc" else TransactionFact.id.desc()
        )
        rows = self.db.execute(query.order_by(order, id_order).offset(
            (page - 1) * page_size
        ).limit(page_size)).mappings().all()
        return [self._fact_values(row) for row in rows], total

    def fact_candidates(self, limit: int) -> list[dict]:
        rows = self.db.execute(
            self._fact_candidate_query().order_by(
                TransactionFact.occurred_time.desc(), TransactionFact.id.desc()
            ).limit(limit)
        ).mappings().all()
        return [self._fact_values(row) for row in rows]

    def idempotency(self, key: str):
        if not key:
            return None
        return self.db.execute(select(
            ReviewRevision.review_case_id.label("case_id"),
            ReviewRevision.operation,
            ReviewRevision.request_json,
        ).where(ReviewRevision.idempotency_key == key)).mappings().one_or_none()

    def version(self, case_id: int) -> int:
        return int(self.db.scalar(select(func.count(ReviewRevision.id)).where(
            ReviewRevision.review_case_id == case_id
        )) or 0)

    def create_draft(
        self,
        *,
        behavior_code: str,
        title: str,
        request_json: str,
        actor: str,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> int:
        case = ReviewCase(
            behavior_type=self._behavior_type(behavior_code),
            status=1,
            title=title,
            created_time=now,
            updated_time=now,
        )
        self.db.add(case)
        self.db.flush()
        self._add_revision(
            case_id=case.id,
            operation=0,
            request_json=request_json,
            before_json="{}",
            after_json=self._canonical({"status": "PENDING"}),
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
        request_json: str,
        actor: str,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> bool:
        case = self.db.get(ReviewCase, case_id)
        if case is None or case.status != 1 or self._has_allocations(case_id):
            return False
        if self.version(case_id) != expected_version:
            return False
        before = self._review_json(self.detail(case_id))
        case.behavior_type = self._behavior_type(behavior_code)
        case.title = title
        case.updated_time = now
        self._add_revision(
            case_id=case.id,
            operation=1,
            request_json=request_json,
            before_json=before,
            after_json=self._canonical({"status": "PENDING"}),
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
            now=now,
        )
        return True

    def latest_plan_payload(self, case_id: int) -> dict | None:
        rows = self.db.scalars(select(ReviewRevision.request_json).where(
            ReviewRevision.review_case_id == case_id,
        ).order_by(ReviewRevision.id.desc())).all()
        for value in rows:
            payload = json.loads(value or "{}")
            if "economics" in payload or "entries" in payload:
                for name in ("operation", "case_id", "expected_version"):
                    payload.pop(name, None)
                return payload
        return None

    def create_defaults(
        self,
        values: list[tuple[TargetReviewFactVO, int, str]],
        now: datetime,
        *,
        request_operation: str = "AUTO_REVIEW",
    ) -> list[int]:
        case_ids = []
        for fact, amount_value, account_code in values:
            case = ReviewCase(
                behavior_type=0,
                status=0,
                title=fact.counterparty or fact.summary or "Default transaction",
                created_time=now,
                updated_time=now,
            )
            entry = LedgerEntry(
                created_time=now,
                **self._ledger_fields(
                    entry_type=0,
                    direction=fact.cash_direction,
                    amount_value=amount_value,
                    amount_scale=fact.amount_scale,
                    currency_code=fact.currency_code,
                    account_code=account_code,
                    occurred_time=fact.occurred_time,
                    now=now,
                ),
            )
            self.db.add_all([case, entry])
            self.db.flush()
            allocation = ReviewAllocation(
                review_case_id=case.id,
                transaction_fact_id=fact.id,
                ledger_entry_id=entry.id,
                amount_value=amount_value,
                amount_scale=fact.amount_scale,
                currency_code=fact.currency_code,
                created_time=now,
                updated_time=now,
            )
            self.db.add(allocation)
            self.db.flush()
            self._add_revision(
                case_id=case.id,
                operation=0,
                request_json=self._canonical({
                    "operation": request_operation,
                    "fact_id": fact.id,
                    "amount_value": amount_value,
                    "behavior_code": "DEFAULT",
                }),
                before_json="{}",
                after_json=self._canonical({"status": "CONFIRMED"}),
                actor="system",
                reason="ensure exact accepted-fact coverage",
                idempotency_key="",
                now=now,
            )
            case_ids.append(case.id)
        return case_ids

    def default_allocations(self, fact_ids: list[int]) -> list[dict]:
        if not fact_ids:
            return []
        rows = self.db.execute(select(
            ReviewAllocation.id,
            ReviewAllocation.review_case_id,
            ReviewAllocation.transaction_fact_id,
            ReviewAllocation.ledger_entry_id,
            ReviewAllocation.amount_value,
            ReviewAllocation.amount_scale,
            ReviewAllocation.currency_code,
        ).join(
            ReviewCase, ReviewCase.id == ReviewAllocation.review_case_id,
        ).where(
            ReviewAllocation.transaction_fact_id.in_(fact_ids),
            ReviewCase.status == 0,
            self._system_case_exists(),
        ).order_by(
            ReviewAllocation.transaction_fact_id, ReviewAllocation.id
        )).mappings().all()
        return [dict(row) for row in rows]

    def confirm_draft(
        self,
        case_id: int,
        *,
        expected_version: int,
        default_rows: list[dict],
        residuals: list[tuple[TargetReviewFactVO, int, str]],
        economics: list[dict],
        allocations: list[dict],
        request_json: str,
        actor: str,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> bool:
        case = self.db.get(ReviewCase, case_id)
        if case is None or case.status != 1 or self._has_allocations(case_id):
            return False
        if self.version(case_id) != expected_version:
            return False
        before = self._review_json(self.detail(case_id))
        self._revoke_defaults(default_rows, case.id, now)
        definitions = {item["client_key"]: item for item in economics}
        rows = []
        for allocation in allocations:
            definition = definitions[allocation["economic_key"]]
            entry = LedgerEntry(
                created_time=now,
                **self._ledger_fields(**definition, now=now),
            )
            self.db.add(entry)
            rows.append((allocation, entry))
        self.db.flush()
        self.db.add_all([
            ReviewAllocation(
                review_case_id=case.id,
                transaction_fact_id=allocation["fact_id"],
                ledger_entry_id=entry.id,
                amount_value=allocation["amount_value"],
                amount_scale=allocation["amount_scale"],
                currency_code=allocation["currency_code"],
                created_time=now,
                updated_time=now,
            )
            for allocation, entry in rows
        ])
        case.status = 0
        case.updated_time = now
        self.create_defaults(residuals, now, request_operation="AUTO_RESIDUAL")
        self.db.flush()
        self._add_revision(
            case_id=case.id,
            operation=1,
            request_json=request_json,
            before_json=before,
            after_json=self._canonical({"status": "CONFIRMED"}),
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
        if case is None or case.status != 0 or self.version(case_id) != expected_version:
            return False
        before = self._review_json(self.detail(case_id))
        case.status = 1
        case.updated_time = now
        self.create_defaults([
            (facts_by_id[fact_id], amount_value, facts_by_id[fact_id].account_code)
            for fact_id, amount_value in sorted(released.items())
        ], now, request_operation="AUTO_RESTORE")
        self.db.flush()
        self._add_revision(
            case_id=case.id,
            operation=2,
            request_json=request_json,
            before_json=before,
            after_json=self._canonical({"status": "REVOKED"}),
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
            now=now,
        )
        return True

    def restore_case(
        self,
        case_id: int,
        expected_version: int,
        default_rows: list[dict],
        residuals: list[tuple[TargetReviewFactVO, int, str]],
        *,
        request_json: str,
        actor: str,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> bool:
        case = self.db.get(ReviewCase, case_id)
        if case is None or case.status != 1 or not self._has_allocations(case_id):
            return False
        if self.version(case_id) != expected_version:
            return False
        before = self._review_json(self.detail(case_id))
        self._revoke_defaults(default_rows, case.id, now)
        case.status = 0
        case.updated_time = now
        self.create_defaults(residuals, now, request_operation="AUTO_RESIDUAL")
        self.db.flush()
        self._add_revision(
            case_id=case.id,
            operation=3,
            request_json=request_json,
            before_json=before,
            after_json=self._canonical({"status": "CONFIRMED"}),
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
            ReviewAllocation.transaction_fact_id,
            func.sum(ReviewAllocation.amount_value).label("amount_value"),
        ).join(
            ReviewCase, ReviewCase.id == ReviewAllocation.review_case_id,
        ).join(
            LedgerEntry, LedgerEntry.id == ReviewAllocation.ledger_entry_id,
        ).where(
            ReviewAllocation.transaction_fact_id.in_(fact_ids),
            ReviewCase.status == 0,
        ).group_by(ReviewAllocation.transaction_fact_id)).mappings().all()
        return {
            row["transaction_fact_id"]: int(row["amount_value"] or 0)
            for row in rows
        }

    def active_economic_facts(self, fact_ids: list[int]) -> dict[int, list[int]]:
        if not fact_ids:
            return {}
        rows = self.db.execute(select(
            ReviewAllocation.ledger_entry_id,
            ReviewAllocation.transaction_fact_id,
        ).join(
            ReviewCase, ReviewCase.id == ReviewAllocation.review_case_id,
        ).where(
            ReviewAllocation.transaction_fact_id.in_(fact_ids),
            ReviewCase.status == 0,
        ).distinct()).mappings().all()
        result = defaultdict(list)
        for row in rows:
            result[row["ledger_entry_id"]].append(row["transaction_fact_id"])
        return dict(result)

    def detail(self, case_id: int) -> TargetEconomicReviewRead | None:
        case = self.db.execute(select(
            ReviewCase.id,
            ReviewCase.behavior_type,
            ReviewCase.status,
            ReviewCase.title,
            ReviewCase.created_time,
            ReviewCase.updated_time,
        ).where(ReviewCase.id == case_id)).mappings().one_or_none()
        if case is None:
            return None
        revision_rows = self.db.execute(select(
            ReviewRevision.id,
            ReviewRevision.operation,
            ReviewRevision.request_json,
            ReviewRevision.before_json,
            ReviewRevision.after_json,
            ReviewRevision.actor,
            ReviewRevision.reason,
            ReviewRevision.idempotency_key,
            ReviewRevision.created_time,
        ).where(ReviewRevision.review_case_id == case_id).order_by(
            ReviewRevision.id
        )).mappings().all()
        plan = self._plan_from_revisions(revision_rows)
        allocation_rows = self.allocations_by_relation(review_ids=[case_id])
        has_persisted_allocations = bool(allocation_rows)
        if has_persisted_allocations:
            economic_ids = [row["economic_id"] for row in allocation_rows]
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
            ).where(LedgerEntry.id.in_(economic_ids)).order_by(
                LedgerEntry.id
            )).mappings().all()
            fact_ids = sorted({row["fact_id"] for row in allocation_rows})
            facts = self.facts(fact_ids)
        else:
            facts, economic_rows, allocation_rows = self._draft_relations(plan)
        history = []
        for version, row in enumerate(revision_rows, 1):
            request = json.loads(row["request_json"] or "{}")
            operation = request.get("operation") or REVISION_OPERATIONS[row["operation"]]
            after_json = row["after_json"] or "{}"
            history.append(TargetReviewHistoryRead(
                id=row["id"],
                version=version,
                operation=operation,
                schema_version=1,
                request=request,
                before=json.loads(row["before_json"] or "{}"),
                after=json.loads(after_json),
                snapshot_hash=hashlib.sha256(after_json.encode()).hexdigest(),
                reverses_history_id=0,
                actor=row["actor"],
                reason=row["reason"],
                idempotency_key=row["idempotency_key"],
                created_time=row["created_time"],
            ))
        behavior_code = plan.get(
            "behavior_code", self._behavior_code(case["behavior_type"])
        )
        result = plan.get("result", {})
        status = self._status_name(case["status"], int(has_persisted_allocations))
        return TargetEconomicReviewRead(
            id=case["id"],
            behavior_code=behavior_code,
            status=status,
            version=max(1, len(revision_rows)),
            title=case["title"],
            result=result,
            facts=[TargetEconomicReviewFactRead(
                id=fact.id,
                occurred_time=fact.occurred_time,
                cash_direction=fact.cash_direction,
                amount_value=fact.amount_value,
                amount_scale=fact.amount_scale,
                currency_code=fact.currency_code,
                account_code=fact.account_code,
                counterparty=fact.counterparty,
                summary=fact.summary,
            ) for fact in facts],
            economics=[TargetEconomicFlowRead(
                id=row["id"],
                economic_type=ECONOMIC_TYPES[row["entry_type"]],
                cash_direction=CASH_DIRECTIONS[row["entry_direction"]],
                amount_value=row["amount_value"],
                amount_scale=row["amount_scale"],
                currency_code=row["currency_code"],
                account_code=row["account_code"],
                counterparty_account_ref=row.get("counterparty_account_ref", ""),
                occurred_time=row["occurred_time"],
            ) for row in economic_rows],
            allocations=[TargetFlowAllocationRead(**row) for row in allocation_rows],
            history=history,
            created_time=case["created_time"],
            updated_time=case["updated_time"],
        )

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()

    def _draft_relations(self, plan: dict):
        allocation_defs = plan.get("allocations", [])
        fact_ids = sorted({
            row.get("fact_id", row.get("transaction_fact_id", 0))
            for row in allocation_defs
            if row.get("fact_id", row.get("transaction_fact_id", 0))
        })
        facts = self.facts(fact_ids)
        fact_by_id = {fact.id: fact for fact in facts}
        definitions = {
            row["client_key"]: row
            for row in plan.get("economics", plan.get("entries", []))
        }
        economics = []
        allocations = []
        for index, row in enumerate(allocation_defs, 1):
            fact_id = row.get("fact_id", row.get("transaction_fact_id"))
            key = row.get("economic_key", row.get("entry_key"))
            definition = definitions[key]
            fact = fact_by_id[fact_id]
            economic_type = definition.get("economic_type")
            if economic_type is None:
                economic_type = ECONOMIC_TYPES[definition["entry_type"]]
            economics.append({
                "id": -index,
                "entry_type": ECONOMIC_TYPE_IDS[economic_type],
                "entry_direction": {
                    "IN": CASH_DIRECTION_IN, "OUT": CASH_DIRECTION_OUT
                }[fact.cash_direction],
                "amount_value": row["amount_value"],
                "amount_scale": fact.amount_scale,
                "currency_code": fact.currency_code,
                "account_code": fact.account_code,
                "counterparty_account_ref": "",
                "occurred_time": fact.occurred_time,
            })
            allocations.append({
                "id": -index,
                "fact_id": fact_id,
                "economic_id": -index,
                "amount_value": row["amount_value"],
                "amount_scale": fact.amount_scale,
                "currency_code": fact.currency_code,
            })
        return facts, economics, allocations

    def _revoke_defaults(
        self, default_rows: list[dict], replacement_case_id: int, now: datetime
    ) -> None:
        case_ids = sorted({row["review_case_id"] for row in default_rows})
        if not case_ids:
            return
        cases = list(self.db.scalars(select(ReviewCase).where(
            ReviewCase.id.in_(case_ids), ReviewCase.status == 0
        )).all())
        ledger_ids = [row["ledger_entry_id"] for row in default_rows]
        if ledger_ids:
            self.db.execute(delete(LedgerEntryTag).where(
                LedgerEntryTag.ledger_id.in_(ledger_ids)
            ))
        for case in cases:
            case.status = 1
            case.updated_time = now
            self._add_revision(
                case_id=case.id,
                operation=2,
                request_json=self._canonical({
                    "operation": "AUTO_REPLACED",
                    "replacement_review_id": replacement_case_id,
                }),
                before_json="{}",
                after_json=self._canonical({"status": "REVOKED"}),
                actor="system",
                reason="amount was reassigned by a confirmed review",
                idempotency_key="",
                now=now,
            )

    def _add_revision(
        self,
        *,
        case_id: int,
        operation: int,
        request_json: str,
        before_json: str,
        after_json: str,
        actor: str,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> None:
        self.db.add(ReviewRevision(
            review_case_id=case_id,
            operation=operation,
            request_json=request_json,
            before_json=before_json,
            after_json=after_json,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
            created_time=now,
            updated_time=now,
        ))
        self.db.flush()

    @staticmethod
    def _ledger_fields(
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
        del client_key
        return {
            "entry_type": entry_type,
            "entry_direction": {"IN": CASH_DIRECTION_IN, "OUT": CASH_DIRECTION_OUT}[
                direction
            ],
            "amount_value": amount_value,
            "amount_scale": amount_scale,
            "currency_code": currency_code,
            "account_code": account_code,
            "counterparty_account_ref": "",
            "occurred_time": occurred_time,
            "updated_time": now,
        }

    def _latest_plan_requests(self, case_ids: list[int]) -> dict[int, dict]:
        if not case_ids:
            return {}
        rows = self.db.execute(select(
            ReviewRevision.review_case_id,
            ReviewRevision.request_json,
        ).where(
            ReviewRevision.review_case_id.in_(case_ids)
        ).order_by(ReviewRevision.id.desc())).mappings().all()
        result = {}
        for row in rows:
            if row["review_case_id"] in result:
                continue
            payload = json.loads(row["request_json"] or "{}")
            if (
                "economics" in payload
                or "entries" in payload
                or payload.get("behavior_code") == "DEFAULT"
            ):
                result[row["review_case_id"]] = payload
        return result

    @staticmethod
    def _plan_from_revisions(rows) -> dict:
        for row in reversed(rows):
            payload = json.loads(row["request_json"] or "{}")
            if "economics" in payload or "entries" in payload:
                return payload
            if payload.get("behavior_code") == "DEFAULT":
                return payload
        return {}

    def _has_allocations(self, case_id: int) -> bool:
        return self.db.scalar(select(exists(select(ReviewAllocation.id).where(
            ReviewAllocation.review_case_id == case_id
        )))) is True

    @staticmethod
    def _status_name(status: int, allocation_count: int) -> str:
        if status == 0:
            return "CONFIRMED"
        return "REVOKED" if allocation_count else "PENDING"

    @staticmethod
    def _behavior_type(behavior_code: str) -> int:
        return 1 if behavior_code in {"ADVANCE", "LOAN", "BORROW_AND_REPAY"} else 0

    @staticmethod
    def _behavior_code(behavior_type: int) -> str:
        return "BORROW_AND_REPAY" if behavior_type == 1 else "TRANSACTION"

    @staticmethod
    def _fact_values(row) -> dict:
        values = dict(row)
        values["cash_direction"] = CASH_DIRECTIONS[values["cash_direction"]]
        return values

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @classmethod
    def _review_json(cls, value: TargetEconomicReviewRead | None) -> str:
        if value is None:
            return "{}"
        return cls._canonical(value.model_dump(
            mode="json", exclude={"history", "created_time", "updated_time"}
        ))

    @staticmethod
    def _system_case_exists():
        return select(ReviewRevision.id).where(
            ReviewRevision.review_case_id == ReviewCase.id,
            ReviewRevision.actor == "system",
        ).exists()
