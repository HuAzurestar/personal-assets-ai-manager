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
from backend.mapper.target_tag_projection_mapper import TargetTagProjectionMapper
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.schema.tag_assignment_request import (
    TagAssignmentBatchRead,
    TagAssignmentItemResult,
    TagAssignmentRequestListBody,
    TagAssignmentRequestListRequest,
    TagAssignmentRequestRead,
    TagAssignmentRequestDetail,
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

    def get(self, request_id: int) -> TagAssignmentRequestDetail:
        TrustedRelationMapper(self.mapper.db).read_snapshot()
        row = self.mapper.read(request_id)
        if row is None:
            raise TargetTagError(404, "标签建议请求不存在", code="TAG_REQUEST_NOT_FOUND")
        self.validate_read_rows([row])
        context = self.approval_context([row])
        code = self.approval_code(row, context)
        return TagAssignmentRequestDetail(**row, eligibility=dict(can_approve=code is None, code=code or "ELIGIBLE"))

    def list(self, request: TagAssignmentRequestListRequest) -> TagAssignmentRequestListBody:
        TrustedRelationMapper(self.mapper.db).read_snapshot()
        sorter_expression = request.sorter[0] if request.sorter else None
        rows, total = self.mapper.list(
            page=request.page_index, page_size=request.page_size,
            filter_value=tag_assignment_request_filter(request),
            sorter=TagAssignmentRequestSorter(order=sorter_expression.direction if sorter_expression else "desc"),
        )
        self.validate_read_rows(rows)
        TargetTagProjectionMapper(self.mapper.db).current_states(sorted({int(row["ledger_id"]) for row in rows}))
        return TagAssignmentRequestListBody(
            items=[TagAssignmentRequestRead(**row) for row in rows],
            total=total, page_index=request.page_index, page_size=request.page_size,
        )

    @staticmethod
    def validate_read_rows(rows):
        if any(row[key] is None for row in rows for key in
               ("rule_name", "view_name", "view_system_name", "proposed_tag_name", "proposed_tag_system_name", "ledger_amount")):
            raise TargetTagError(409, "suggestion reference is damaged", code="TAG_RELATION_BROKEN")

    def approval_context(self, rows):
        self.validate_read_rows(self.mapper.read_by_ids([int(row["id"]) for row in rows]))
        ledger_ids = {int(row["ledger_id"]) for row in rows}
        view_ids = {int(row["view_id"]) for row in rows}
        scopes = {(int(row["ledger_id"]), int(row["view_id"])) for row in rows}
        projection = TargetTagProjectionMapper(self.mapper.db)
        projection.active_dictionary()
        projection.current_states(sorted(ledger_ids))
        return dict(rules=self.mapper.rule_rows({int(row["rule_id"]) for row in rows}),
            scopes=Counter((int(row["ledger_id"]), int(row["view_id"])) for row in rows),
            active_ledgers=self.mapper.active_ledger_ids(ledger_ids), types=self.mapper.ledger_types(ledger_ids),
            active_views=self.mapper.active_view_ids(view_ids),
            targets=self.mapper.active_dictionary_rows({int(row["proposed_tag_id"]) for row in rows}),
            scope_tags=self.mapper.scope_tag_rows(scopes), enabled_sources=self.mapper.enabled_sources(scopes))

    @staticmethod
    def approval_code(row, context):
        if row["status"] not in (TAG_REQUEST_STATUS_PENDING, TAG_REQUEST_STATUS_ENABLED):
            return "REQUEST_STATE_CONFLICT"
        scope = (int(row["ledger_id"]), int(row["view_id"]))
        rule = context["rules"].get(int(row["rule_id"]))
        current = [item for item in context["scope_tags"].get(scope, []) if item[3] == "ACTIVE"]
        target = context["targets"].get(int(row["proposed_tag_id"]))
        if context["scopes"][scope] > 1:
            return "SCOPE_CONFLICT"
        if rule is None or rule.rule_revision != row["rule_revision"] or rule.view_id != scope[1]:
            return "RULE_STALE"
        if context["types"].get(scope[0]) == 3:
            return "LEDGER_DUPLICATE"
        if scope[0] not in context["active_ledgers"]:
            return "LEDGER_INACTIVE"
        if scope[1] not in context["active_views"]:
            return "VIEW_INACTIVE"
        if target is None or target[0] != scope[1] or target[1] == "unclassified":
            return "TAG_INACTIVE"
        sources = context["enabled_sources"].get(scope, [])
        if row["status"] == TAG_REQUEST_STATUS_ENABLED:
            return "ALREADY_APPROVED" if len(current) == 1 and current[0][1] == row["proposed_tag_id"] and sources == [row["proposed_tag_id"]] else "SUGGESTION_STALE"
        if len(current) != 1 or current[0][2] != "unclassified" or sources:
            return "MANUAL_TAG_CONFLICT"
        return None

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
            if accepted:
                context = self.approval_context(rows)
                rules, scope_tags = context["rules"], context["scope_tags"]
            else:
                rules = self.mapper.rule_rows({int(row["rule_id"]) for row in rows})
                scope_tags = {}

            results: dict[int, TagAssignmentItemResult] = {}
            eligible = []
            for request_id in request_ids:
                row = by_id.get(request_id)
                code = None
                status = int(row["status"]) if row is not None else None
                if row is None:
                    code = "NOT_FOUND"
                elif accepted:
                    code = self.approval_code(row, context)
                    if code is None:
                        eligible.append(row)
                elif status == TAG_REQUEST_STATUS_REJECTED:
                    code = "ALREADY_REJECTED"
                elif status != TAG_REQUEST_STATUS_PENDING:
                    code = "REQUEST_STATE_CONFLICT"
                else:
                    rule = rules.get(int(row["rule_id"]))
                    if rule is None:
                        code = "RULE_STALE"
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
