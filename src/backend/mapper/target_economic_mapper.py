from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.entity import (
    CASH_DIRECTION_IN,
    CASH_DIRECTION_OUT,
    LedgerEntry,
    ReviewAllocation,
    ReviewCase,
    ReviewRevision,
    TransactionFact,
)
from backend.schema.target_review import (
    TargetEconomicFlowRead,
    TargetEconomicReviewRead,
    TargetFlowAllocationRead,
    TargetReviewFactVO,
    TargetReviewRevisionRead,
)


class TargetEconomicMapper:
    """Set-oriented persistence for the physical Review and Ledger schema."""

    def __init__(self, db: Session):
        self.db = db

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

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
        return tuple(TargetReviewFactVO(
            **self._fact_values(row)
        ) for row in rows)

    def review_page(
        self,
        page: int,
        page_size: int,
        status: int | None = None,
    ) -> tuple[list[dict], int]:
        clauses = [~self._system_case_exists()]
        if status is not None:
            clauses.append(ReviewCase.status == status)
        grouped = select(
            ReviewCase.id,
            ReviewCase.behavior_type,
            ReviewCase.status,
            ReviewCase.title,
            ReviewCase.created_time,
            ReviewCase.updated_time,
            func.count(ReviewAllocation.id).label("allocation_count"),
        ).join(
            ReviewAllocation,
            ReviewAllocation.review_case_id == ReviewCase.id,
        ).where(*clauses).group_by(ReviewCase.id)
        total = int(self.db.scalar(
            select(func.count()).select_from(grouped.subquery())
        ) or 0)
        rows = self.db.execute(grouped.order_by(
            ReviewCase.updated_time.desc(),
            ReviewCase.id.desc(),
        ).offset((page - 1) * page_size).limit(page_size)).mappings().all()
        return [
            {
                **dict(row),
                "ledger_entry_count": row["allocation_count"],
            }
            for row in rows
        ], total

    def _fact_candidate_query(self):
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
        ).where(
            ReviewCase.status == 0,
            self._system_case_exists(),
        ).group_by(TransactionFact.id).order_by(
            TransactionFact.occurred_time.desc(),
            TransactionFact.id.desc(),
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
        return [self._fact_values(row) for row in rows], int(total)

    def fact_candidates(self, limit: int) -> list[dict]:
        rows = self.db.execute(
            self._fact_candidate_query().limit(limit)
        ).mappings().all()
        return [self._fact_values(row) for row in rows]

    def idempotency(self, key: str):
        if not key:
            return None
        return self.db.execute(select(
            ReviewRevision.review_case_id,
            ReviewRevision.operation,
            ReviewRevision.request_json,
        ).where(
            ReviewRevision.idempotency_key == key
        )).mappings().one_or_none()

    def create_defaults(
        self,
        values: list[tuple[TargetReviewFactVO, int, str]],
        now: datetime,
        *,
        request_operation: str = "AUTO_REVIEW",
    ) -> list[int]:
        if not values:
            return []
        cases = [
            ReviewCase(
                behavior_type=0,
                status=0,
                title=fact.counterparty or fact.summary or "默认交易",
                created_time=now,
                updated_time=now,
            )
            for fact, _amount, _account_code in values
        ]
        self.db.add_all(cases)
        self.db.flush()

        entries = []
        for fact, amount_value, account_code in values:
            entries.append(LedgerEntry(
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
            ))
        self.db.add_all(entries)
        self.db.flush()

        allocations = [
            ReviewAllocation(
                review_case_id=case.id,
                transaction_fact_id=fact.id,
                ledger_entry_id=entry.id,
                amount_value=amount_value,
                amount_scale=fact.amount_scale,
                currency_code=fact.currency_code,
                created_time=now,
                updated_time=now,
            )
            for case, entry, (fact, amount_value, _account_code)
            in zip(cases, entries, values)
        ]
        self.db.add_all(allocations)
        self.db.flush()

        revisions = []
        for case, entry, allocation, (fact, amount_value, _account_code) in zip(
            cases,
            entries,
            allocations,
            values,
        ):
            revisions.append(ReviewRevision(
                review_case_id=case.id,
                operation=0,
                request_json=self._canonical({
                    "operation": request_operation,
                    "transaction_fact_id": fact.id,
                    "amount_value": amount_value,
                }),
                before_json="{}",
                after_json=self._snapshot(case, [entry], [allocation]),
                actor="system",
                reason="ensure every accepted fact has complete ledger coverage",
                idempotency_key="",
                created_time=now,
                updated_time=now,
            ))
        self.db.add_all(revisions)
        self.db.flush()
        return [case.id for case in cases]

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
            ReviewCase,
            ReviewCase.id == ReviewAllocation.review_case_id,
        ).join(
            LedgerEntry,
            LedgerEntry.id == ReviewAllocation.ledger_entry_id,
        ).where(
            ReviewAllocation.transaction_fact_id.in_(fact_ids),
            ReviewCase.status == 0,
            self._system_case_exists(),
        ).order_by(
            ReviewAllocation.transaction_fact_id,
            ReviewAllocation.id,
        )).mappings().all()
        return [dict(row) for row in rows]

    def create_published(
        self,
        *,
        behavior_type: int,
        title: str,
        default_rows: list[dict],
        residuals: list[tuple[TargetReviewFactVO, int, str]],
        ledger_definitions: list[dict],
        allocations: list[dict],
        request_json: str,
        actor: str,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> int:
        case = ReviewCase(
            behavior_type=behavior_type,
            status=0,
            title=title,
            created_time=now,
            updated_time=now,
        )
        self.db.add(case)
        self.db.flush()

        self._revoke_defaults(default_rows, case.id, now)
        definitions = {item["client_key"]: item for item in ledger_definitions}
        entries = []
        for allocation in allocations:
            definition = definitions[allocation["ledger_key"]]
            entry = LedgerEntry(
                created_time=now,
                **self._ledger_fields(**definition, now=now),
            )
            self.db.add(entry)
            entries.append((allocation, entry))
        self.db.flush()

        allocation_rows = [
            ReviewAllocation(
                review_case_id=case.id,
                transaction_fact_id=allocation["transaction_fact_id"],
                ledger_entry_id=entry.id,
                amount_value=allocation["amount_value"],
                amount_scale=allocation["amount_scale"],
                currency_code=allocation["currency_code"],
                created_time=now,
                updated_time=now,
            )
            for allocation, entry in entries
        ]
        self.db.add_all(allocation_rows)
        self.db.flush()
        self.create_defaults(
            residuals,
            now,
            request_operation="AUTO_RESIDUAL",
        )
        self.db.flush()

        entry_rows = [entry for _allocation, entry in entries]
        self._add_revision(
            case=case,
            operation=0,
            request_json=request_json,
            before_json="{}",
            after_json=self._snapshot(case, entry_rows, allocation_rows),
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
            now=now,
        )
        return case.id

    def revoke_case(
        self,
        case_id: int,
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
        if case is None or case.status != 0:
            return False
        before_json = self._review_json(self.detail(case_id))
        case.status = 1
        case.updated_time = now
        self.db.flush()
        self.create_defaults([
            (
                facts_by_id[fact_id],
                amount_value,
                facts_by_id[fact_id].account_code,
            )
            for fact_id, amount_value in sorted(released.items())
        ], now, request_operation="AUTO_RESTORE")
        self.db.flush()
        self._add_revision(
            case=case,
            operation=2,
            request_json=request_json,
            before_json=before_json,
            after_json=self._review_json(self.detail(case_id)),
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
            now=now,
        )
        return True

    def restore_case(
        self,
        case_id: int,
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
        if case is None or case.status != 1:
            return False
        before_json = self._review_json(self.detail(case_id))
        self._revoke_defaults(default_rows, case.id, now)
        case.status = 0
        case.updated_time = now
        self.create_defaults(
            residuals,
            now,
            request_operation="AUTO_RESIDUAL",
        )
        self.db.flush()
        self._add_revision(
            case=case,
            operation=3,
            request_json=request_json,
            before_json=before_json,
            after_json=self._review_json(self.detail(case_id)),
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
            ReviewCase,
            ReviewCase.id == ReviewAllocation.review_case_id,
        ).join(
            LedgerEntry,
            LedgerEntry.id == ReviewAllocation.ledger_entry_id,
        ).where(
            ReviewAllocation.transaction_fact_id.in_(fact_ids),
            ReviewCase.status == 0,
        ).group_by(
            ReviewAllocation.transaction_fact_id
        )).mappings().all()
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
            ReviewCase,
            ReviewCase.id == ReviewAllocation.review_case_id,
        ).join(
            LedgerEntry,
            LedgerEntry.id == ReviewAllocation.ledger_entry_id,
        ).where(
            ReviewAllocation.transaction_fact_id.in_(fact_ids),
            ReviewCase.status == 0,
        ).distinct().order_by(
            ReviewAllocation.ledger_entry_id,
            ReviewAllocation.transaction_fact_id,
        )).mappings().all()
        result: dict[int, list[int]] = defaultdict(list)
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

        allocations = self.db.execute(select(
            ReviewAllocation.id,
            ReviewAllocation.transaction_fact_id,
            ReviewAllocation.ledger_entry_id,
            ReviewAllocation.amount_value,
            ReviewAllocation.amount_scale,
            ReviewAllocation.currency_code,
        ).where(
            ReviewAllocation.review_case_id == case_id
        ).order_by(ReviewAllocation.id)).mappings().all()
        ledger_ids = [row["ledger_entry_id"] for row in allocations]
        ledger_entries = self.db.execute(select(
            LedgerEntry.id,
            LedgerEntry.entry_type,
            LedgerEntry.entry_direction,
            LedgerEntry.amount_value,
            LedgerEntry.amount_scale,
            LedgerEntry.currency_code,
            LedgerEntry.account_code,
            LedgerEntry.counterparty_account_ref,
            LedgerEntry.occurred_time,
        ).where(
            LedgerEntry.id.in_(ledger_ids)
        ).order_by(LedgerEntry.id)).mappings().all() if ledger_ids else []

        revisions = self.db.execute(select(
            ReviewRevision.id,
            ReviewRevision.operation,
            ReviewRevision.request_json,
            ReviewRevision.before_json,
            ReviewRevision.after_json,
            ReviewRevision.actor,
            ReviewRevision.reason,
            ReviewRevision.idempotency_key,
            ReviewRevision.created_time,
        ).where(
            ReviewRevision.review_case_id == case_id
        ).order_by(ReviewRevision.id)).mappings().all()
        return TargetEconomicReviewRead(
            id=case["id"],
            behavior_type=case["behavior_type"],
            status=case["status"],
            title=case["title"],
            ledger_entries=[TargetEconomicFlowRead(**row) for row in ledger_entries],
            allocations=[TargetFlowAllocationRead(**row) for row in allocations],
            history=[TargetReviewRevisionRead(
                id=row["id"],
                operation=row["operation"],
                request=json.loads(row["request_json"] or "{}"),
                before=json.loads(row["before_json"] or "{}"),
                after=json.loads(row["after_json"] or "{}"),
                actor=row["actor"],
                reason=row["reason"],
                idempotency_key=row["idempotency_key"],
                created_time=row["created_time"],
            ) for row in revisions],
            created_time=case["created_time"],
            updated_time=case["updated_time"],
        )

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()

    def _revoke_defaults(
        self,
        default_rows: list[dict],
        replacement_case_id: int,
        now: datetime,
    ) -> None:
        case_ids = sorted({row["review_case_id"] for row in default_rows})
        if not case_ids:
            return
        cases = list(self.db.scalars(select(ReviewCase).where(
            ReviewCase.id.in_(case_ids),
            ReviewCase.status == 0,
        )).all())
        rows_by_case: dict[int, list[dict]] = defaultdict(list)
        for row in default_rows:
            rows_by_case[row["review_case_id"]].append(dict(row))
        for case in cases:
            before_json = self._canonical({
                "id": case.id,
                "status": case.status,
                "allocations": rows_by_case[case.id],
            })
            case.status = 1
            case.updated_time = now
            after_json = self._canonical({
                "id": case.id,
                "status": case.status,
                "allocations": rows_by_case[case.id],
            })
            self._add_revision(
                case=case,
                operation=2,
                request_json=self._canonical({
                    "operation": "AUTO_REPLACED",
                    "replacement_review_case_id": replacement_case_id,
                }),
                before_json=before_json,
                after_json=after_json,
                actor="system",
                reason="amount was reassigned by a confirmed review",
                idempotency_key="",
                now=now,
            )

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
        direction_code = {
            "IN": CASH_DIRECTION_IN,
            "OUT": CASH_DIRECTION_OUT,
        }.get(direction)
        if direction_code is None:
            raise ValueError(f"unknown cash direction: {direction}")
        return {
            "entry_type": entry_type,
            "entry_direction": direction_code,
            "amount_value": amount_value,
            "amount_scale": amount_scale,
            "currency_code": currency_code,
            "account_code": account_code,
            "counterparty_account_ref": "",
            "occurred_time": occurred_time,
            "updated_time": now,
        }

    def _add_revision(
        self,
        *,
        case: ReviewCase,
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
            review_case_id=case.id,
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
    def _snapshot(
        case: ReviewCase,
        entries: list[LedgerEntry],
        allocations: list[ReviewAllocation],
    ) -> str:
        return TargetEconomicMapper._canonical({
            "id": case.id,
            "behavior_type": case.behavior_type,
            "status": case.status,
            "title": case.title,
            "ledger_entries": [{
                "id": row.id,
                "entry_type": row.entry_type,
                "entry_direction": row.entry_direction,
                "amount_value": row.amount_value,
                "amount_scale": row.amount_scale,
                "currency_code": row.currency_code,
                "account_code": row.account_code,
                "counterparty_account_ref": row.counterparty_account_ref,
                "occurred_time": row.occurred_time,
            } for row in entries],
            "allocations": [{
                "id": row.id,
                "transaction_fact_id": row.transaction_fact_id,
                "ledger_entry_id": row.ledger_entry_id,
                "amount_value": row.amount_value,
                "amount_scale": row.amount_scale,
                "currency_code": row.currency_code,
            } for row in allocations],
        })

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
            mode="json",
            exclude={"history", "created_time", "updated_time"},
        ))

    @staticmethod
    def _system_case_exists():
        return select(ReviewRevision.id).where(
            ReviewRevision.review_case_id == ReviewCase.id,
            ReviewRevision.actor == "system",
        ).exists()

    @staticmethod
    def _fact_values(row) -> dict:
        values = dict(row)
        direction = {
            CASH_DIRECTION_IN: "IN",
            CASH_DIRECTION_OUT: "OUT",
        }.get(values["cash_direction"])
        if direction is None:
            raise ValueError(
                f"unknown transaction_fact cash_direction: "
                f"{values['cash_direction']}"
            )
        values["cash_direction"] = direction
        return values
