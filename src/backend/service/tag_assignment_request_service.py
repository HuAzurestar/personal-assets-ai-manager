from __future__ import annotations

from collections import Counter
from datetime import timedelta

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.entity import (
    MAX_COUNTER_VALUE,
    TAG_REQUEST_STATUS_ENABLED,
    TAG_REQUEST_STATUS_PENDING,
    TAG_REQUEST_STATUS_REJECTED,
)
from backend.entity.base import utc_now
from backend.error import TargetTagError
from backend.mapper.tag_assignment_request_mapper import TagAssignmentRequestMapper
from backend.schema.tag_assignment_request import (
    TagAssignmentBatchRead,
    TagAssignmentItemResult,
    TagAssignmentRequestListBody,
    TagAssignmentRequestListRequest,
    TagAssignmentRequestRead,
    TagAssignmentRequestSorter,
    tag_assignment_request_filter,
)


class TagAssignmentRequestService:
    """Validate each scope under one short write lock; commit valid scopes together.

    Business conflicts are per-item results. Unexpected storage failures still
    roll back EVERY write. No per-item SQL, model call, or automatic retry.
    """

    def __init__(self, db: Session):
        self.mapper = TagAssignmentRequestMapper(db)

    def get(self, request_id: int) -> TagAssignmentRequestRead:
        row = self.mapper.read(request_id)
        if row is None:
            raise TargetTagError(404, "标签建议请求不存在", code="TAG_REQUEST_NOT_FOUND")
        return TagAssignmentRequestRead(**row)

    def list(self, request: TagAssignmentRequestListRequest) -> TagAssignmentRequestListBody:
        sorter_expression = request.sorter[0] if request.sorter else None
        rows, total = self.mapper.list(
            page=request.page_index, page_size=request.page_size,
            filter_value=tag_assignment_request_filter(request),
            sorter=TagAssignmentRequestSorter(order=sorter_expression.direction if sorter_expression else "desc"),
        )
        return TagAssignmentRequestListBody(
            items=[TagAssignmentRequestRead(**row) for row in rows],
            total=total, page_index=request.page_index, page_size=request.page_size,
        )

    def approve(self, request_ids: list[int]) -> TagAssignmentBatchRead:
        return self._transition(request_ids, accepted=True)

    def reject(self, request_ids: list[int]) -> TagAssignmentBatchRead:
        return self._transition(request_ids, accepted=False)

    def _transition(self, request_ids: list[int], *, accepted: bool) -> TagAssignmentBatchRead:
        # The service is used outside HTTP too; enforce the public batch bounds.
        if (not request_ids or len(request_ids) > 100 or
                any(type(value) is not int or not 1 <= value <= MAX_COUNTER_VALUE for value in request_ids) or
                len(set(request_ids)) != len(request_ids)):
            raise TargetTagError(422, "请选择1至100条不同的请求", code="TAG_REQUEST_BATCH_INVALID")
        try:
            self.mapper.begin_write()
            rows = self.mapper.by_ids(request_ids)
            by_id = {int(row["id"]): row for row in rows}
            rules = self.mapper.rule_rows({int(row["rule_id"]) for row in rows})
            scopes = {(int(row["ledger_id"]), int(row["view_id"])) for row in rows}
            scope_counts = Counter((int(row["ledger_id"]), int(row["view_id"])) for row in rows)
            if accepted:
                active_ledgers = self.mapper.active_ledger_ids({int(row["ledger_id"]) for row in rows})
                active_views = self.mapper.active_view_ids({int(row["view_id"]) for row in rows})
                targets = self.mapper.active_dictionary_rows({int(row["proposed_tag_id"]) for row in rows})
                scope_tags = self.mapper.scope_tag_rows(scopes)
                enabled_sources = self.mapper.enabled_sources(scopes)
            else:
                scope_tags = {}

            results: dict[int, TagAssignmentItemResult] = {}
            eligible = []
            for request_id in request_ids:
                row = by_id.get(request_id)
                code = None
                status = int(row["status"]) if row is not None else None
                if row is None:
                    code = "NOT_FOUND"
                elif status == (TAG_REQUEST_STATUS_ENABLED if accepted else TAG_REQUEST_STATUS_REJECTED):
                    code = "ALREADY_APPROVED" if accepted else "ALREADY_REJECTED"
                elif status != TAG_REQUEST_STATUS_PENDING:
                    code = "REQUEST_STATE_CONFLICT"
                else:
                    rule = rules.get(int(row["rule_id"]))
                    scope = (int(row["ledger_id"]), int(row["view_id"]))
                    if rule is None:
                        code = "RULE_STALE"
                    elif accepted:
                        current = [item for item in scope_tags.get(scope, []) if item[3] == "ACTIVE"]
                        target = targets.get(int(row["proposed_tag_id"]))
                        if scope_counts[scope] > 1:
                            code = "SCOPE_CONFLICT"
                        elif rule.rule_revision != row["rule_revision"] or rule.view_id != row["view_id"]:
                            code = "RULE_STALE"
                        elif scope[0] not in active_ledgers:
                            code = "LEDGER_INACTIVE"
                        elif scope[1] not in active_views:
                            code = "VIEW_INACTIVE"
                        elif target is None or target[0] != scope[1] or target[1] == "unclassified":
                            code = "TAG_INACTIVE"
                        elif len(current) != 1 or not (
                            (current[0][2] == "unclassified" and not enabled_sources.get(scope))
                            or enabled_sources.get(scope) == [current[0][1]]
                        ):
                            code = "MANUAL_TAG_CONFLICT"
                    if code is None:
                        eligible.append(row)
                if code is not None:
                    results[request_id] = TagAssignmentItemResult(request_id=request_id, result=code, status=status)

            # Overflow rejects only that rule's subset. SQLite never promotes
            # counters to REAL and other rules do not lose their decisions.
            field = "accepted_count" if accepted else "rejected_count"
            counts = Counter(int(row["rule_id"]) for row in eligible)
            exhausted = {
                rule_id for rule_id, delta in counts.items()
                if int(getattr(rules[rule_id], field)) > MAX_COUNTER_VALUE - delta
            }
            valid = []
            for row in eligible:
                if int(row["rule_id"]) in exhausted:
                    results[int(row["id"])] = TagAssignmentItemResult(
                        request_id=int(row["id"]), result="COUNTER_EXHAUSTED", status=TAG_REQUEST_STATUS_PENDING,
                    )
                else:
                    valid.append(row)
            if valid:
                now = utc_now()
                previous = self.mapper.latest_assignment_time({int(row["ledger_id"]) for row in valid}) if accepted else None
                if previous is not None:
                    now = max(now, previous + timedelta(microseconds=1))
                for rule_id, delta in Counter(int(row["rule_id"]) for row in valid).items():
                    rule = rules[rule_id]
                    setattr(rule, field, int(getattr(rule, field)) + delta)
                    rule.updated_time = max(now, rule.updated_time + timedelta(microseconds=1))
                if accepted:
                    self.mapper.approve_rows(valid, scope_tags, now=now)
                else:
                    self.mapper.reject_rows([int(row["id"]) for row in valid], now=now)
                for row in valid:
                    results[int(row["id"])] = TagAssignmentItemResult(
                        request_id=int(row["id"]), result="APPROVED" if accepted else "REJECTED",
                        status=TAG_REQUEST_STATUS_ENABLED if accepted else TAG_REQUEST_STATUS_REJECTED,
                    )
            self.mapper.commit()
            return TagAssignmentBatchRead(
                operation="APPROVE" if accepted else "REJECT",
                items=[results[request_id] for request_id in request_ids],
            )
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetTagError(
                409, "标签建议写入冲突，请刷新核对后重试", code="TAG_REQUEST_WRITE_CONFLICT",
            ) from error
        except Exception:
            self.mapper.rollback()
            raise
