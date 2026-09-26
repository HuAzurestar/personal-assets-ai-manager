from __future__ import annotations

from collections import Counter
from datetime import timedelta

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.entity import MAX_COUNTER_VALUE, TAG_REQUEST_STATUS_PENDING
from backend.entity.base import utc_now
from backend.error import TargetTagError
from backend.mapper.tag_assignment_request_mapper import TagAssignmentRequestMapper
from backend.schema.tag_assignment_request import (
    TagAssignmentBatchRead,
    TagAssignmentRequestListBody,
    TagAssignmentRequestListRequest,
    TagAssignmentRequestRead,
    TagAssignmentRequestSorter,
    tag_assignment_request_filter,
)


class TagAssignmentRequestService:
    """Review automatic tag suggestions under one short SQLite write lock."""

    def __init__(self, db: Session):
        self.mapper = TagAssignmentRequestMapper(db)

    def get(self, request_id: int) -> TagAssignmentRequestRead:
        row = self.mapper.read(request_id)
        if row is None:
            raise TargetTagError(
                404,
                "标签建议请求不存在",
                code="TAG_REQUEST_NOT_FOUND",
            )
        return TagAssignmentRequestRead(**row)

    def list(
        self,
        request: TagAssignmentRequestListRequest,
    ) -> TagAssignmentRequestListBody:
        sorter_expression = request.sorter[0] if request.sorter else None
        sorter = TagAssignmentRequestSorter(
            order=sorter_expression.direction if sorter_expression else "desc",
        )
        rows, total = self.mapper.list(
            page=request.page_index,
            page_size=request.page_size,
            filter_value=tag_assignment_request_filter(request),
            sorter=sorter,
        )
        return TagAssignmentRequestListBody(
            items=[TagAssignmentRequestRead(**row) for row in rows],
            total=total,
            page_index=request.page_index,
            page_size=request.page_size,
        )

    def approve(self, request_ids: list[int]) -> TagAssignmentBatchRead:
        try:
            self.mapper.begin_write()
            rows = self._pending_rows(request_ids)
            scopes = [
                (int(row["ledger_id"]), int(row["view_id"]))
                for row in rows
            ]
            if len(scopes) != len(set(scopes)):
                raise TargetTagError(
                    409,
                    "同一 Ledger 与标签维度只能选择一条建议",
                    code="TAG_REQUEST_SCOPE_CONFLICT",
                )
            scope_set = set(scopes)
            rule_rows = self.mapper.rule_rows({int(row["rule_id"]) for row in rows})
            active_ledgers = self.mapper.active_ledger_ids({
                int(row["ledger_id"]) for row in rows
            })
            dictionary = self.mapper.active_dictionary_rows({
                int(row["proposed_tag_id"]) for row in rows
            })
            scope_rows = self.mapper.scope_tag_rows(scope_set)
            enabled_scopes = self.mapper.enabled_scopes(scope_set)
            for row in rows:
                request_id = int(row["id"])
                rule = rule_rows.get(int(row["rule_id"]))
                if (
                    rule is None
                    or rule.rule_revision != int(row["rule_revision"])
                ):
                    self._conflict(request_id, "RULE_REVISION_CHANGED")
                if rule.view_id != int(row["view_id"]):
                    self._conflict(request_id, "RULE_VIEW_CHANGED")
                if int(row["ledger_id"]) not in active_ledgers:
                    self._conflict(request_id, "LEDGER_INACTIVE")
                target = dictionary.get(int(row["proposed_tag_id"]))
                if (
                    target is None
                    or target[0] != int(row["view_id"])
                    or target[1] == "unclassified"
                ):
                    self._conflict(request_id, "TARGET_TAG_INACTIVE")
                scope = (int(row["ledger_id"]), int(row["view_id"]))
                current = [
                    item for item in scope_rows.get(scope, []) if item[3] == "ACTIVE"
                ]
                if len(current) != 1 or current[0][2] != "unclassified":
                    self._conflict(request_id, "MANUAL_TAG_CONFLICT")
                if scope in enabled_scopes:
                    self._conflict(request_id, "SOURCE_ALREADY_ENABLED")

            now = utc_now()
            self._increment_counters(rule_rows, rows, accepted=True, now=now)
            self.mapper.approve_rows(rows, scope_rows, now=now)
            items = [
                TagAssignmentRequestRead(**row)
                for row in self.mapper.read_by_ids(request_ids)
            ]
            self.mapper.commit()
            return TagAssignmentBatchRead(
                operation="APPROVE",
                items=items,
            )
        except TargetTagError:
            self.mapper.rollback()
            raise
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetTagError(
                409,
                "标签建议状态已变化，请刷新后重试",
                code="TAG_REQUEST_WRITE_CONFLICT",
            ) from error
        except Exception:
            self.mapper.rollback()
            raise

    def reject(self, request_ids: list[int]) -> TagAssignmentBatchRead:
        try:
            self.mapper.begin_write()
            rows = self._pending_rows(request_ids)
            rule_rows = self.mapper.rule_rows({int(row["rule_id"]) for row in rows})
            if len(rule_rows) != len({int(row["rule_id"]) for row in rows}):
                raise TargetTagError(
                    409,
                    "来源规则已不存在，请刷新核对",
                    code="TAG_REQUEST_RULE_MISSING",
                )
            now = utc_now()
            self._increment_counters(rule_rows, rows, accepted=False, now=now)
            self.mapper.reject_rows(request_ids, now=now)
            items = [
                TagAssignmentRequestRead(**row)
                for row in self.mapper.read_by_ids(request_ids)
            ]
            self.mapper.commit()
            return TagAssignmentBatchRead(
                operation="REJECT",
                items=items,
            )
        except TargetTagError:
            self.mapper.rollback()
            raise
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetTagError(
                409,
                "标签建议状态已变化，请刷新后重试",
                code="TAG_REQUEST_WRITE_CONFLICT",
            ) from error
        except Exception:
            self.mapper.rollback()
            raise

    def _pending_rows(self, request_ids: list[int]) -> list[dict[str, object]]:
        rows = self.mapper.by_ids(request_ids)
        found = {int(row["id"]) for row in rows}
        missing = sorted(set(request_ids) - found)
        if missing:
            raise TargetTagError(
                404,
                f"标签建议请求不存在：{missing}",
                code="TAG_REQUEST_NOT_FOUND",
            )
        non_pending = sorted(
            int(row["id"])
            for row in rows
            if row["status"] != TAG_REQUEST_STATUS_PENDING
        )
        if non_pending:
            raise TargetTagError(
                409,
                f"标签建议已不再处于待确认状态：{non_pending}",
                code="TAG_REQUEST_NOT_PENDING",
            )
        by_id = {int(row["id"]): row for row in rows}
        return [by_id[request_id] for request_id in request_ids]

    @staticmethod
    def _increment_counters(rule_rows, rows, *, accepted: bool, now) -> None:
        counts = Counter(int(row["rule_id"]) for row in rows)
        field = "accepted_count" if accepted else "rejected_count"
        for rule_id, delta in counts.items():
            rule = rule_rows[rule_id]
            current = int(getattr(rule, field))
            if current > MAX_COUNTER_VALUE - delta:
                raise TargetTagError(
                    409,
                    f"自动标签规则计数器 {field} 已达到上限",
                    code="AUTO_TAG_RULE_COUNTER_EXHAUSTED",
                )
            setattr(rule, field, current + delta)
            rule.updated_time = max(
                now,
                rule.updated_time + timedelta(microseconds=1),
            )

    @staticmethod
    def _conflict(request_id: int, reason: str) -> None:
        raise TargetTagError(
            409,
            f"标签建议请求 {request_id} 已不再适用，请刷新核对",
            code="TAG_REQUEST_STALE",
            details={"request_id": request_id, "reason": reason},
        )
