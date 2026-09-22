from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from backend.entity import (
    TAG_REQUEST_STATUS_PENDING,
    TAG_REQUEST_STATUS_CANCELLED,
    TagAssignmentRequest,
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
