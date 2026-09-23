from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    delete,
    distinct,
    exists,
    func,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.orm import Session

from backend.entity import (
    TAG_REQUEST_STATUS_CANCELLED,
    TAG_REQUEST_STATUS_ENABLED,
    TAG_REQUEST_STATUS_PENDING,
    TAG_REQUEST_STATUS_REJECTED,
    AutoTagRule,
    LedgerEntry,
    LedgerEntryTag,
    ReviewAllocation,
    ReviewCase,
    TagAssignmentRequest,
    TargetTag,
    TargetTagView,
    TransactionFact,
)
from backend.schema.tag_assignment_request import (
    TagAssignmentRequestFilter,
    TagAssignmentRequestSorter,
)


class TagAssignmentRequestMapper:
    """Explicit-column persistence for automatic tag assignment requests."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _columns():
        return (
            TagAssignmentRequest.id,
            TagAssignmentRequest.rule_id,
            TagAssignmentRequest.rule_revision,
            TagAssignmentRequest.ledger_id,
            TagAssignmentRequest.view_id,
            TagAssignmentRequest.proposed_tag_id,
            TagAssignmentRequest.status,
            TagAssignmentRequest.reason_summary,
            TagAssignmentRequest.created_time,
            TagAssignmentRequest.updated_time,
        )

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def get(self, request_id: int) -> dict[str, object] | None:
        row = self.db.execute(select(*self._columns()).where(
            TagAssignmentRequest.id == request_id,
        )).mappings().one_or_none()
        return dict(row) if row is not None else None

    def by_ids(self, request_ids: list[int]) -> list[dict[str, object]]:
        if not request_ids:
            return []
        rows = self.db.execute(select(*self._columns()).where(
            TagAssignmentRequest.id.in_(request_ids),
        ).order_by(TagAssignmentRequest.id)).mappings().all()
        return [dict(row) for row in rows]

    @staticmethod
    def _read_columns():
        first_fact_id = select(ReviewAllocation.transaction_fact_id).where(
            ReviewAllocation.ledger_entry_id == TagAssignmentRequest.ledger_id,
        ).order_by(ReviewAllocation.id).limit(1).correlate(
            TagAssignmentRequest,
        ).scalar_subquery()
        return (
            TagAssignmentRequest.id,
            TagAssignmentRequest.rule_id,
            TagAssignmentRequest.rule_revision,
            AutoTagRule.name.label("rule_name"),
            TagAssignmentRequest.ledger_id,
            select(TransactionFact.summary).where(
                TransactionFact.id == first_fact_id,
            ).scalar_subquery().label("ledger_summary"),
            select(TransactionFact.counterparty_name).where(
                TransactionFact.id == first_fact_id,
            ).scalar_subquery().label("ledger_counterparty_name"),
            LedgerEntry.amount.label("ledger_amount"),
            LedgerEntry.currency_code.label("ledger_currency_code"),
            exists(select(ReviewAllocation.id).join(
                ReviewCase,
                ReviewCase.id == ReviewAllocation.review_case_id,
            ).where(
                ReviewAllocation.ledger_entry_id == TagAssignmentRequest.ledger_id,
                ReviewCase.status == 0,
            )).label("ledger_active"),
            TagAssignmentRequest.view_id,
            TargetTagView.name.label("view_name"),
            TargetTagView.system_name.label("view_system_name"),
            TagAssignmentRequest.proposed_tag_id,
            TargetTag.name.label("proposed_tag_name"),
            TargetTag.system_name.label("proposed_tag_system_name"),
            TagAssignmentRequest.status,
            TagAssignmentRequest.reason_summary,
            TagAssignmentRequest.created_time,
            TagAssignmentRequest.updated_time,
        )

    @staticmethod
    def _read_statement():
        return select(*TagAssignmentRequestMapper._read_columns()).join(
            AutoTagRule,
            AutoTagRule.id == TagAssignmentRequest.rule_id,
        ).join(
            TargetTagView,
            TargetTagView.id == TagAssignmentRequest.view_id,
        ).join(
            TargetTag,
            TargetTag.id == TagAssignmentRequest.proposed_tag_id,
        ).outerjoin(
            LedgerEntry,
            LedgerEntry.id == TagAssignmentRequest.ledger_id,
        )

    def read(self, request_id: int) -> dict[str, object] | None:
        row = self.db.execute(self._read_statement().where(
            TagAssignmentRequest.id == request_id,
        )).mappings().one_or_none()
        return dict(row) if row is not None else None

    def read_by_ids(self, request_ids: list[int]) -> list[dict[str, object]]:
        if not request_ids:
            return []
        rows = self.db.execute(self._read_statement().where(
            TagAssignmentRequest.id.in_(request_ids),
        ).order_by(TagAssignmentRequest.id)).mappings().all()
        return [dict(row) for row in rows]

    def list(
        self,
        *,
        page: int,
        page_size: int,
        filter_value: TagAssignmentRequestFilter,
        sorter: TagAssignmentRequestSorter,
    ) -> tuple[list[dict[str, object]], int]:
        clauses = []
        for name in ("rule_id", "view_id", "ledger_id", "status"):
            value = getattr(filter_value, name)
            if value is not None:
                clauses.append(getattr(TagAssignmentRequest, name) == value)
        total = int(self.db.scalar(select(
            func.count(TagAssignmentRequest.id),
        ).where(*clauses)) or 0)
        order = (
            TagAssignmentRequest.created_time.asc()
            if sorter.order == "asc"
            else TagAssignmentRequest.created_time.desc()
        )
        id_order = (
            TagAssignmentRequest.id.asc()
            if sorter.order == "asc"
            else TagAssignmentRequest.id.desc()
        )
        rows = self.db.execute(self._read_statement().where(
            *clauses,
        ).order_by(
            order,
            id_order,
        ).offset((page - 1) * page_size).limit(page_size)).mappings().all()
        return [dict(row) for row in rows], total

    def rule_rows(self, rule_ids: set[int]) -> dict[int, AutoTagRule]:
        if not rule_ids:
            return {}
        rows = self.db.scalars(select(AutoTagRule).where(
            AutoTagRule.id.in_(rule_ids),
        )).all()
        return {row.id: row for row in rows}

    def active_ledger_ids(self, ledger_ids: set[int]) -> set[int]:
        if not ledger_ids:
            return set()
        return set(self.db.scalars(select(
            distinct(ReviewAllocation.ledger_entry_id),
        ).join(
            ReviewCase,
            ReviewCase.id == ReviewAllocation.review_case_id,
        ).where(
            ReviewAllocation.ledger_entry_id.in_(ledger_ids),
            ReviewCase.status == 0,
        )).all())

    def active_dictionary_rows(
        self,
        tag_ids: set[int],
    ) -> dict[int, tuple[int, str]]:
        if not tag_ids:
            return {}
        rows = self.db.execute(select(
            TargetTag.id,
            TargetTag.view_id,
            TargetTag.system_name,
        ).join(
            TargetTagView,
            TargetTagView.id == TargetTag.view_id,
        ).where(
            TargetTag.id.in_(tag_ids),
            TargetTag.status == "ACTIVE",
            TargetTagView.status == "ACTIVE",
        )).all()
        return {
            tag_id: (view_id, system_name)
            for tag_id, view_id, system_name in rows
        }

    def scope_tag_rows(
        self,
        scopes: set[tuple[int, int]],
    ) -> dict[tuple[int, int], list[tuple[int, int, str, str, datetime]]]:
        if not scopes:
            return {}
        rows = self.db.execute(select(
            LedgerEntryTag.id,
            LedgerEntryTag.ledger_id,
            TargetTag.view_id,
            TargetTag.id.label("tag_id"),
            TargetTag.system_name,
            TargetTag.status,
            LedgerEntryTag.updated_time,
        ).join(
            TargetTag,
            TargetTag.id == LedgerEntryTag.tag_id,
        ).where(
            tuple_(LedgerEntryTag.ledger_id, TargetTag.view_id).in_(scopes),
        ).order_by(
            LedgerEntryTag.ledger_id,
            TargetTag.view_id,
            TargetTag.id,
        )).all()
        result: dict[
            tuple[int, int],
            list[tuple[int, int, str, str, datetime]],
        ] = {}
        for (
            row_id,
            ledger_id,
            view_id,
            tag_id,
            system_name,
            status,
            updated_time,
        ) in rows:
            result.setdefault((ledger_id, view_id), []).append(
                (row_id, tag_id, system_name, status, updated_time)
            )
        return result

    def enabled_scopes(
        self,
        scopes: set[tuple[int, int]],
    ) -> set[tuple[int, int]]:
        if not scopes:
            return set()
        return set(self.db.execute(select(
            TagAssignmentRequest.ledger_id,
            TagAssignmentRequest.view_id,
        ).where(
            tuple_(
                TagAssignmentRequest.ledger_id,
                TagAssignmentRequest.view_id,
            ).in_(scopes),
            TagAssignmentRequest.status == TAG_REQUEST_STATUS_ENABLED,
        )).all())

    def approve_rows(
        self,
        rows: list[dict[str, object]],
        scope_rows: dict[
            tuple[int, int],
            list[tuple[int, int, str, str, datetime]],
        ],
        *,
        now: datetime,
    ) -> None:
        request_ids = [int(row["id"]) for row in rows]
        scopes = {(int(row["ledger_id"]), int(row["view_id"])) for row in rows}
        link_ids = [
            item[0]
            for scope in scopes
            for item in scope_rows.get(scope, [])
        ]
        if link_ids:
            self.db.execute(delete(LedgerEntryTag).where(
                LedgerEntryTag.id.in_(link_ids),
            ))
        self.db.add_all([
            LedgerEntryTag(
                ledger_id=int(row["ledger_id"]),
                tag_id=int(row["proposed_tag_id"]),
                created_time=now,
                updated_time=now,
            )
            for row in rows
        ])
        self.db.execute(update(TagAssignmentRequest).where(
            tuple_(
                TagAssignmentRequest.ledger_id,
                TagAssignmentRequest.view_id,
            ).in_(scopes),
            TagAssignmentRequest.id.not_in(request_ids),
            TagAssignmentRequest.status == TAG_REQUEST_STATUS_PENDING,
        ).values(
            status=TAG_REQUEST_STATUS_CANCELLED,
            updated_time=now,
        ))
        self.db.execute(update(TagAssignmentRequest).where(
            TagAssignmentRequest.id.in_(request_ids),
            TagAssignmentRequest.status == TAG_REQUEST_STATUS_PENDING,
        ).values(
            status=TAG_REQUEST_STATUS_ENABLED,
            updated_time=now,
        ))

    def reject_rows(self, request_ids: list[int], *, now: datetime) -> None:
        self.db.execute(update(TagAssignmentRequest).where(
            TagAssignmentRequest.id.in_(request_ids),
            TagAssignmentRequest.status == TAG_REQUEST_STATUS_PENDING,
        ).values(
            status=TAG_REQUEST_STATUS_REJECTED,
            updated_time=now,
        ))

    def create_many(
        self,
        requests: list[dict[str, object]],
        now: datetime,
    ) -> list[int]:
        entities = [
            TagAssignmentRequest(
                rule_id=int(item["rule_id"]),
                rule_revision=int(item["rule_revision"]),
                ledger_id=int(item["ledger_id"]),
                view_id=int(item["view_id"]),
                proposed_tag_id=int(item["proposed_tag_id"]),
                status=TAG_REQUEST_STATUS_PENDING,
                reason_summary=str(item.get("reason_summary", "")),
                created_time=now,
                updated_time=now,
            )
            for item in requests
        ]
        self.db.add_all(entities)
        self.db.flush()
        return [entity.id for entity in entities]

    def cancel_pending_for_rule(
        self,
        rule_id: int,
        *,
        before_revision: int,
        now: datetime,
    ) -> int:
        result = self.db.execute(
            update(TagAssignmentRequest)
            .where(
                TagAssignmentRequest.rule_id == rule_id,
                TagAssignmentRequest.rule_revision < before_revision,
                TagAssignmentRequest.status == TAG_REQUEST_STATUS_PENDING,
            )
            .values(
                status=TAG_REQUEST_STATUS_CANCELLED,
                updated_time=now,
            )
        )
        return int(result.rowcount or 0)

    def cancel_pending_for_rule_ids(
        self,
        rule_ids: list[int],
        *,
        now: datetime,
    ) -> int:
        if not rule_ids:
            return 0
        result = self.db.execute(
            update(TagAssignmentRequest)
            .where(
                TagAssignmentRequest.rule_id.in_(rule_ids),
                TagAssignmentRequest.status == TAG_REQUEST_STATUS_PENDING,
            )
            .values(
                status=TAG_REQUEST_STATUS_CANCELLED,
                updated_time=now,
            )
        )
        return int(result.rowcount or 0)

    def cancel_pending_for_ledger_ids(
        self,
        ledger_ids: list[int],
        *,
        now: datetime,
    ) -> int:
        if not ledger_ids:
            return 0
        result = self.db.execute(
            update(TagAssignmentRequest)
            .where(
                TagAssignmentRequest.ledger_id.in_(ledger_ids),
                TagAssignmentRequest.status == TAG_REQUEST_STATUS_PENDING,
            )
            .values(
                status=TAG_REQUEST_STATUS_CANCELLED,
                updated_time=now,
            )
        )
        return int(result.rowcount or 0)

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
