from __future__ import annotations

from dataclasses import asdict

from sqlalchemy import bindparam, insert, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.errors import TagCommandError
from app.database import Bill, TagAudit, TagView, ViewTag
from app.schemas.tagging import (
    TagAuditVO,
    TagAuditWriteVO,
    TagCommandBillVO,
    TagValueVO,
    TagViewVO,
)


class TagMapper:
    """Set-based SQL for tag commands; no query is issued from row loops."""

    def __init__(self, db: Session):
        self.db = db

    def begin_immediate(self) -> None:
        try:
            self.db.execute(text("BEGIN IMMEDIATE"))
        except OperationalError as error:
            self.db.rollback()
            raise TagCommandError(409, "Ledger is busy; retry the review operation") from error

    def bills(self, bill_ids: list[int]) -> dict[int, TagCommandBillVO]:
        rows = self.db.execute(
            select(Bill.id, Bill.category, Bill.tag_state_json)
            .where(Bill.id.in_(bill_ids))
            .order_by(Bill.id)
        ).mappings().all()
        return {row["id"]: TagCommandBillVO(
            id=row["id"],
            category=row["category"],
            tag_state_json=row["tag_state_json"],
        ) for row in rows}

    def tag_dictionary(self) -> tuple[tuple[TagViewVO, ...], tuple[TagValueVO, ...]]:
        view_rows = self.db.execute(
            select(TagView.id, TagView.name, TagView.system_name)
            .where(TagView.archived.is_(False))
            .order_by(TagView.id)
        ).mappings().all()
        tag_rows = self.db.execute(
            select(
                ViewTag.id,
                ViewTag.view_id,
                ViewTag.name,
                ViewTag.system_name,
                ViewTag.is_unclassified,
            )
            .where(ViewTag.archived.is_(False))
            .order_by(ViewTag.view_id, ViewTag.id)
        ).mappings().all()
        return (
            tuple(TagViewVO(**row) for row in view_rows),
            tuple(TagValueVO(**row) for row in tag_rows),
        )

    def current_audits(self, bill_ids: list[int]) -> dict[int, tuple[TagAuditVO, ...]]:
        rows = self.db.execute(
            select(
                TagAudit.id,
                TagAudit.bill_id,
                TagAudit.idempotency_key,
                TagAudit.request_payload,
                TagAudit.undone,
            )
            .where(
                TagAudit.bill_id.in_(bill_ids),
                TagAudit.superseded.is_(False),
            )
            .order_by(TagAudit.bill_id, TagAudit.id.desc())
        ).mappings().all()
        grouped: dict[int, list[TagAuditVO]] = {}
        for row in rows:
            grouped.setdefault(row["bill_id"], []).append(TagAuditVO(**row))
        return {bill_id: tuple(audits) for bill_id, audits in grouped.items()}

    def audits_by_idempotency_keys(self, keys: list[str]) -> dict[int, TagAuditVO]:
        if not keys:
            return {}
        rows = self.db.execute(
            select(
                TagAudit.id,
                TagAudit.bill_id,
                TagAudit.idempotency_key,
                TagAudit.request_payload,
                TagAudit.undone,
            ).where(TagAudit.idempotency_key.in_(keys))
        ).mappings().all()
        return {row["bill_id"]: TagAuditVO(**row) for row in rows}

    def persist(
        self,
        *,
        superseded_audit_ids: list[int],
        bill_updates: list[dict],
        audit_writes: list[TagAuditWriteVO],
    ) -> None:
        if superseded_audit_ids:
            self.db.execute(
                update(TagAudit)
                .where(TagAudit.id.in_(superseded_audit_ids))
                .values(superseded=True)
            )
        if bill_updates:
            statement = (
                update(Bill.__table__)
                .where(Bill.id == bindparam("target_bill_id"))
                .values(
                    category=bindparam("category_value"),
                    tag_state_json=bindparam("tag_state_value"),
                )
            )
            self.db.execute(statement, bill_updates)
        if audit_writes:
            self.db.execute(
                insert(TagAudit.__table__),
                [asdict(audit) for audit in audit_writes],
            )
