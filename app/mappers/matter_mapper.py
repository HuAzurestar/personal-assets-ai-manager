from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, insert, select, text, union_all, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.errors import MatterCommandError
from app.database import Bill, RefundAllocation, RefundDesignation, ReviewMatter, ReviewMatterRevision
from app.schemas.matter import (
    CurrentMatterVO,
    MatterBillVO,
    MatterRevisionVO,
    MatterSummaryVO,
    MatterVO,
)


class MatterMapper:
    """All SQL for manual Review matters, expressed as bounded set queries."""

    def __init__(self, db: Session):
        self.db = db

    def begin_immediate(self) -> None:
        try:
            self.db.execute(text("BEGIN IMMEDIATE"))
        except OperationalError as error:
            self.db.rollback()
            raise MatterCommandError(409, "账本正在写入，请稍后重试") from error

    def matters(self, matter_ids: list[int] | None = None) -> tuple[MatterVO, ...]:
        statement = select(
            ReviewMatter.id,
            ReviewMatter.version,
            ReviewMatter.created_at,
        )
        if matter_ids is not None:
            if not matter_ids:
                return ()
            statement = statement.where(ReviewMatter.id.in_(matter_ids))
        rows = self.db.execute(statement.order_by(ReviewMatter.id.desc())).mappings().all()
        return tuple(MatterVO(**row) for row in rows)

    def matter(self, matter_id: int) -> MatterVO | None:
        matters = self.matters([matter_id])
        return matters[0] if matters else None

    def page(self, *, page: int, page_size: int) -> tuple[int, tuple[MatterSummaryVO, ...]]:
        total = self.db.scalar(select(func.count(ReviewMatter.id))) or 0
        rows = self.db.execute(
            select(
                ReviewMatterRevision.matter_id,
                ReviewMatterRevision.version,
                ReviewMatterRevision.action,
                func.json_extract(ReviewMatterRevision.snapshot, "$.title").label("title"),
                func.json_extract(ReviewMatterRevision.snapshot, "$.scenarios").label("scenarios"),
                func.json_extract(
                    ReviewMatterRevision.snapshot,
                    "$.own_accounts_confirmed",
                ).label("own_accounts_confirmed"),
                func.json_extract(ReviewMatterRevision.snapshot, "$.balances").label("balances"),
                func.json_array_length(
                    ReviewMatterRevision.snapshot,
                    "$.lines",
                ).label("line_count"),
            )
            .join(ReviewMatter, (
                (ReviewMatter.id == ReviewMatterRevision.matter_id)
                & (ReviewMatter.version == ReviewMatterRevision.version)
            ))
            .order_by(ReviewMatter.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).mappings().all()
        return total, tuple(MatterSummaryVO(**row) for row in rows)

    def revisions(self, matter_ids: list[int]) -> dict[int, tuple[MatterRevisionVO, ...]]:
        if not matter_ids:
            return {}
        rows = self.db.execute(
            select(
                ReviewMatterRevision.id,
                ReviewMatterRevision.matter_id,
                ReviewMatterRevision.version,
                ReviewMatterRevision.action,
                ReviewMatterRevision.snapshot,
                ReviewMatterRevision.reason,
                ReviewMatterRevision.actor,
                ReviewMatterRevision.idempotency_key,
                ReviewMatterRevision.request_payload,
                ReviewMatterRevision.created_at,
            )
            .where(ReviewMatterRevision.matter_id.in_(matter_ids))
            .order_by(ReviewMatterRevision.matter_id, ReviewMatterRevision.version)
        ).mappings().all()
        grouped: dict[int, list[MatterRevisionVO]] = {}
        for row in rows:
            grouped.setdefault(row["matter_id"], []).append(MatterRevisionVO(**row))
        return {matter_id: tuple(items) for matter_id, items in grouped.items()}

    def bills(self, bill_ids: list[int]) -> dict[int, MatterBillVO]:
        if not bill_ids:
            return {}
        rows = self.db.execute(
            select(
                Bill.id,
                Bill.occurred_at,
                Bill.merchant,
                Bill.amount,
                Bill.account_name,
                Bill.aggregate_excluded,
            ).where(Bill.id.in_(bill_ids))
        ).mappings().all()
        return {row["id"]: MatterBillVO(**row) for row in rows}

    def current_revisions(self) -> tuple[CurrentMatterVO, ...]:
        rows = self.db.execute(
            select(
                ReviewMatterRevision.matter_id,
                ReviewMatterRevision.version,
                ReviewMatterRevision.action,
                ReviewMatterRevision.snapshot,
            )
            .join(ReviewMatter, (
                (ReviewMatter.id == ReviewMatterRevision.matter_id)
                & (ReviewMatter.version == ReviewMatterRevision.version)
            ))
            .order_by(ReviewMatterRevision.matter_id)
        ).mappings().all()
        return tuple(CurrentMatterVO(**row) for row in rows)

    def revision_by_idempotency_key(self, key: str) -> MatterRevisionVO | None:
        row = self.db.execute(
            select(
                ReviewMatterRevision.id,
                ReviewMatterRevision.matter_id,
                ReviewMatterRevision.version,
                ReviewMatterRevision.action,
                ReviewMatterRevision.snapshot,
                ReviewMatterRevision.reason,
                ReviewMatterRevision.actor,
                ReviewMatterRevision.idempotency_key,
                ReviewMatterRevision.request_payload,
                ReviewMatterRevision.created_at,
            ).where(ReviewMatterRevision.idempotency_key == key)
        ).mappings().one_or_none()
        return MatterRevisionVO(**row) if row else None

    def refund_bill_ids(self, bill_ids: list[int]) -> set[int]:
        if not bill_ids:
            return set()
        statement = union_all(
            select(RefundDesignation.bill_id.label("bill_id"))
            .where(RefundDesignation.bill_id.in_(bill_ids)),
            select(RefundAllocation.refund_bill_id.label("bill_id"))
            .where(
                RefundAllocation.status == "confirmed",
                RefundAllocation.refund_bill_id.in_(bill_ids),
            ),
            select(RefundAllocation.expense_bill_id.label("bill_id"))
            .where(
                RefundAllocation.status == "confirmed",
                RefundAllocation.expense_bill_id.in_(bill_ids),
            ),
        )
        return set(self.db.scalars(statement).all())

    def create_matter(self, *, created_at: datetime) -> MatterVO:
        matter = ReviewMatter(version=1, created_at=created_at)
        self.db.add(matter)
        self.db.flush()
        return MatterVO(id=matter.id, version=1, created_at=created_at)

    def advance_version(self, matter_id: int, expected_version: int) -> int:
        next_version = expected_version + 1
        result = self.db.execute(
            update(ReviewMatter)
            .where(
                ReviewMatter.id == matter_id,
                ReviewMatter.version == expected_version,
            )
            .values(version=next_version)
        )
        if result.rowcount != 1:
            raise MatterCommandError(409, "事项已被其他操作修改，请重新打开最新版本")
        return next_version

    def append_revision(
        self,
        *,
        matter_id: int,
        version: int,
        action: str,
        snapshot: str,
        reason: str,
        idempotency_key: str,
        request_payload: str,
        created_at: datetime,
    ) -> None:
        self.db.execute(insert(ReviewMatterRevision).values(
            matter_id=matter_id,
            version=version,
            action=action,
            snapshot=snapshot,
            reason=reason,
            actor="local-user",
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            created_at=created_at,
        ))
