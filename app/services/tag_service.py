from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.errors import TagCommandError
from app.mappers.tag_mapper import TagMapper
from app.schemas import TagStateBulkAssignmentRequest
from app.schemas.tagging import TagAuditWriteVO, TagStateBulkResult, TagValueVO, TagViewVO


TAG_STATE_MAX_LENGTH = 2048
SYSTEM_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class TagService:
    def __init__(self, db: Session):
        self.db = db
        self.mapper = TagMapper(db)

    def assign_bulk(self, payload: TagStateBulkAssignmentRequest) -> TagStateBulkResult:
        self.mapper.begin_immediate()
        try:
            requested_ids = set(payload.bill_ids)
            bills = self.mapper.bills(payload.bill_ids)
            if len(bills) != len(requested_ids):
                raise TagCommandError(404, "One or more transactions were not found")

            ordered_ids = sorted(bills)
            views, tags = self.mapper.tag_dictionary()
            current_audits = self.mapper.current_audits(ordered_ids)
            encoded_request = json.dumps(
                payload.model_dump(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            audit_keys = self._audit_keys(ordered_ids, payload.idempotency_key)
            existing = self.mapper.audits_by_idempotency_keys(list(audit_keys.values()))

            if existing:
                self._validate_replay(
                    ordered_ids,
                    audit_keys,
                    encoded_request,
                    existing,
                    current_audits,
                )
                self.db.commit()
                return TagStateBulkResult(updated=len(ordered_ids), bill_ids=ordered_ids)

            self._validate_revisions(ordered_ids, payload.expected_revisions, current_audits)
            views_by_name = {view.system_name: view for view in views}
            tags_by_key = {(tag.view_id, tag.system_name): tag for tag in tags}
            defaults = self._default_state(views, tags)

            suggestion = payload.strategy in {"local_rules", "llm_suggestion"}
            superseded_ids: list[int] = []
            bill_updates: list[dict] = []
            audit_writes: list[TagAuditWriteVO] = []
            now = datetime.now()
            for bill_id in ordered_ids:
                bill = bills[bill_id]
                before_state = self._parse_state(bill.tag_state_json) or defaults.copy()
                submitted = (
                    {**before_state, **payload.tag_state}
                    if payload.merge
                    else payload.tag_state
                )
                state = self._validate_state(submitted, views, views_by_name, tags_by_key, defaults)
                state_json = self._state_json(state)
                selected = [tags_by_key[(view.id, state[view.system_name])] for view in views]
                category = next(
                    (tag.name for view, tag in zip(views, selected) if view.system_name == "category"),
                    bill.category,
                )
                if not suggestion:
                    superseded_ids.extend(audit.id for audit in current_audits.get(bill_id, ()))
                    bill_updates.append({
                        "target_bill_id": bill_id,
                        "category_value": category,
                        "tag_state_value": state_json,
                    })
                audit_writes.append(TagAuditWriteVO(
                    bill_id=bill_id,
                    category=category,
                    tags=",".join(tag.name for tag in selected),
                    tag_state_json=state_json,
                    strategy=payload.strategy,
                    confidence=payload.confidence,
                    provider="named_tag_state_bulk",
                    superseded=suggestion,
                    action="suggest" if suggestion else "confirm",
                    actor="local-user",
                    reason=payload.reason,
                    before_state_json=self._state_json(before_state),
                    before_category=bill.category,
                    idempotency_key=audit_keys.get(bill_id),
                    request_payload=encoded_request,
                    created_at=now,
                ))

            self.mapper.persist(
                superseded_audit_ids=superseded_ids,
                bill_updates=bill_updates,
                audit_writes=audit_writes,
            )
            self.db.commit()
            return TagStateBulkResult(updated=len(ordered_ids), bill_ids=ordered_ids)
        except Exception:
            self.db.rollback()
            raise

    @staticmethod
    def _audit_keys(bill_ids: list[int], idempotency_key: str | None) -> dict[int, str]:
        if not idempotency_key:
            return {}
        return {
            bill_id: f"bulk-tag-{hashlib.sha256(f'{idempotency_key}:{bill_id}'.encode()).hexdigest()}"
            for bill_id in bill_ids
        }

    @staticmethod
    def _latest_id(current_audits, bill_id: int) -> int:
        audits = current_audits.get(bill_id, ())
        return audits[0].id if audits else 0

    def _validate_replay(
        self,
        bill_ids,
        audit_keys,
        encoded_request,
        existing,
        current_audits,
    ) -> None:
        if set(existing) != set(bill_ids):
            raise TagCommandError(409, "批量标签请求只保存了部分结果，请核验审计后使用新请求重试")
        for bill_id in bill_ids:
            audit = existing[bill_id]
            if (
                audit.idempotency_key != audit_keys[bill_id]
                or audit.request_payload != encoded_request
                or audit.undone
                or audit.id != self._latest_id(current_audits, bill_id)
            ):
                raise TagCommandError(409, "批量标签请求或当前状态已发生变化，请刷新后重试")

    def _validate_revisions(self, bill_ids, expected_revisions, current_audits) -> None:
        if expected_revisions is None:
            return
        for bill_id in bill_ids:
            if expected_revisions.get(bill_id) != self._latest_id(current_audits, bill_id):
                raise TagCommandError(409, f"流水 {bill_id} 的标签已有修改，本次批量操作未生效，请刷新")

    @staticmethod
    def _parse_state(encoded: str) -> dict[str, str]:
        try:
            state = json.loads(encoded or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return state if isinstance(state, dict) else {}

    @staticmethod
    def _default_state(views: tuple[TagViewVO, ...], tags: tuple[TagValueVO, ...]) -> dict[str, str]:
        defaults = {}
        for view in views:
            tag = next((item for item in tags if item.view_id == view.id and item.is_unclassified), None)
            if not tag:
                raise TagCommandError(500, f"Tag view {view.system_name} has no unclassified tag")
            defaults[view.system_name] = tag.system_name
        return defaults

    def _validate_state(self, submitted, views, views_by_name, tags_by_key, defaults) -> dict[str, str]:
        if not isinstance(submitted, dict):
            raise TagCommandError(422, "tag_state must be a JSON object")
        try:
            submitted_json = self._state_json(submitted)
        except (TypeError, ValueError) as error:
            raise TagCommandError(422, "tag_state must be JSON serializable") from error
        if len(submitted_json) > TAG_STATE_MAX_LENGTH:
            raise TagCommandError(422, f"tag_state must be at most {TAG_STATE_MAX_LENGTH} bytes")

        state: dict[str, str] = {}
        for view_name, tag_name in submitted.items():
            if not isinstance(view_name, str) or not isinstance(tag_name, str):
                raise TagCommandError(422, "tag_state keys and values must be system-name strings")
            self._validate_system_name(view_name, "Tag view")
            self._validate_system_name(tag_name, "Tag")
            view = views_by_name.get(view_name)
            if not view:
                raise TagCommandError(422, f"Unknown or archived tag view: {view_name}")
            if (view.id, tag_name) not in tags_by_key:
                raise TagCommandError(422, f"Unknown or archived tag in {view_name}: {tag_name}")
            state[view_name] = tag_name
        for view in views:
            state.setdefault(view.system_name, defaults[view.system_name])

        encoded = self._state_json(state)
        if len(encoded) > TAG_STATE_MAX_LENGTH:
            raise TagCommandError(422, f"tag_state must be at most {TAG_STATE_MAX_LENGTH} bytes")
        return state

    @staticmethod
    def _validate_system_name(value: str, noun: str) -> None:
        if not SYSTEM_NAME_PATTERN.fullmatch(value):
            raise TagCommandError(422, f"{noun} system_name must use lowercase letters, numbers, and underscores")

    @staticmethod
    def _state_json(state: dict[str, str]) -> str:
        return json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
