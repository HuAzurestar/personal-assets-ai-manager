from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.entity import ReviewCase, ReviewCaseBill, ReviewHistory
from backend.schema.target_review import (
    TargetReviewCaseRead,
    TargetReviewHistoryRead,
    TargetReviewIdempotencyVO,
    TargetReviewLineRead,
)


class TargetReviewMapper:
    """Explicit, set-oriented SQL boundary for target Review commands."""

    def __init__(self, db: Session):
        self.db = db

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def idempotency(self, key: str) -> TargetReviewIdempotencyVO | None:
        row = self.db.execute(select(
            ReviewHistory.case_id,
            ReviewHistory.operation,
            ReviewHistory.request_json,
        ).where(ReviewHistory.idempotency_key == key)).mappings().one_or_none()
        return TargetReviewIdempotencyVO(**row) if row else None

    def transition(
        self,
        case_id: int,
        *,
        status: str,
        expected_version: int,
        now: datetime,
    ) -> int | None:
        case = self.db.get(ReviewCase, case_id)
        if case is None or case.version != expected_version:
            return None
        case.status = status
        case.version += 1
        case.updated_time = now
        self.db.flush()
        return case.version

    def add_history(
        self,
        *,
        case_id: int,
        version: int,
        operation: str,
        request_json: str,
        before_json: str,
        after_json: str,
        snapshot_hash: str,
        reverses_history_id: int,
        actor: str,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> int:
        history = ReviewHistory(
            case_id=case_id,
            version=version,
            operation=operation,
            schema_version=1,
            request_json=request_json,
            before_json=before_json,
            after_json=after_json,
            snapshot_hash=snapshot_hash,
            reverses_history_id=reverses_history_id,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
            created_time=now,
            updated_time=now,
        )
        self.db.add(history)
        self.db.flush()
        return history.id

    def detail(self, case_id: int) -> TargetReviewCaseRead | None:
        case = self.db.execute(select(
            ReviewCase.id,
            ReviewCase.review_type,
            ReviewCase.status,
            ReviewCase.allocation_status,
            ReviewCase.version,
            ReviewCase.title,
            ReviewCase.result_json,
            ReviewCase.created_time,
            ReviewCase.updated_time,
        ).where(ReviewCase.id == case_id)).mappings().one_or_none()
        if case is None:
            return None
        lines = self._lines([case_id]).get(case_id, [])
        history = self._history([case_id]).get(case_id, [])
        return self._case_read(case, lines, history)

    def page(
        self,
        page: int,
        page_size: int,
        status: str = "",
        review_type: str = "",
    ) -> tuple[list[TargetReviewCaseRead], int]:
        clauses = [ReviewCase.behavior_code != "DEFAULT"]
        if status:
            clauses.append(ReviewCase.status == status)
        if review_type:
            clauses.append(ReviewCase.review_type == review_type)
        total = self.db.scalar(select(func.count(ReviewCase.id)).where(*clauses)) or 0
        cases = self.db.execute(select(
            ReviewCase.id,
            ReviewCase.review_type,
            ReviewCase.status,
            ReviewCase.allocation_status,
            ReviewCase.version,
            ReviewCase.title,
            ReviewCase.result_json,
            ReviewCase.created_time,
            ReviewCase.updated_time,
        ).where(*clauses).order_by(ReviewCase.id.desc()).offset(
            (page - 1) * page_size
        ).limit(page_size)).mappings().all()
        case_ids = [row["id"] for row in cases]
        lines = self._lines(case_ids)
        return ([
            self._case_read(row, lines.get(row["id"], []), [])
            for row in cases
        ], total)

    def _lines(self, case_ids: list[int]) -> dict[int, list[TargetReviewLineRead]]:
        if not case_ids:
            return {}
        rows = self.db.execute(select(
            ReviewCaseBill.id,
            ReviewCaseBill.case_id,
            ReviewCaseBill.bill_id,
            ReviewCaseBill.role,
            ReviewCaseBill.party,
            ReviewCaseBill.amount_value,
            ReviewCaseBill.amount_scale,
            ReviewCaseBill.currency_code,
        ).where(ReviewCaseBill.case_id.in_(case_ids)).order_by(
            ReviewCaseBill.case_id,
            ReviewCaseBill.id,
        )).mappings().all()
        result: dict[int, list[TargetReviewLineRead]] = {}
        for row in rows:
            case_id = row["case_id"]
            result.setdefault(case_id, []).append(TargetReviewLineRead(
                **{key: value for key, value in row.items() if key != "case_id"}
            ))
        return result

    def _history(self, case_ids: list[int]) -> dict[int, list[TargetReviewHistoryRead]]:
        if not case_ids:
            return {}
        rows = self.db.execute(select(
            ReviewHistory.id,
            ReviewHistory.case_id,
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
        ).where(ReviewHistory.case_id.in_(case_ids)).order_by(
            ReviewHistory.case_id,
            ReviewHistory.version,
        )).mappings().all()
        result: dict[int, list[TargetReviewHistoryRead]] = {}
        for row in rows:
            case_id = row["case_id"]
            result.setdefault(case_id, []).append(TargetReviewHistoryRead(
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
            ))
        return result

    @staticmethod
    def _case_read(case, lines, history) -> TargetReviewCaseRead:
        return TargetReviewCaseRead(
            id=case["id"],
            review_type=case["review_type"],
            status=case["status"],
            allocation_status=case["allocation_status"],
            version=case["version"],
            title=case["title"],
            result=json.loads(case["result_json"]),
            lines=lines,
            history=history,
            created_time=case["created_time"],
            updated_time=case["updated_time"],
        )

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
