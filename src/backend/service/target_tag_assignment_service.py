from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.error import TargetTagError
from backend.entity.base import utc_now
from backend.mapper.target_tag_assignment_mapper import TargetTagAssignmentMapper
from backend.mapper.tag_assignment_request_mapper import TagAssignmentRequestMapper
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.schema.target_tag import TargetTagAssignmentRead, TargetTagAssignmentRequest
from backend.service.target_tag_projection_service import TargetTagProjectionService


class TargetTagAssignmentService:
    """Replace the effective Tag state owned directly by one Ledger."""

    def __init__(self, db: Session):
        self.mapper = TargetTagAssignmentMapper(db)
        self.requests = TagAssignmentRequestMapper(db)
        self.projection = TargetTagProjectionService(db)

    def get(self, ledger_id: int) -> TargetTagAssignmentRead:
        TrustedRelationMapper(self.mapper.db).read_snapshot()
        if not self.mapper.ledger_exists(ledger_id):
            raise TargetTagError(404, "ledger entry not found")
        current = self.mapper.assignment(ledger_id)
        return TargetTagAssignmentRead(
            ledger_id=ledger_id,
            tag_state=self.projection.effective_state(current["tag_state"]),
            updated_time=current["updated_time"],
        )

    def assign(
        self,
        ledger_id: int,
        payload: TargetTagAssignmentRequest,
    ) -> TargetTagAssignmentRead:
        try:
            self.mapper.begin_write()
            status = self.mapper.ledger_status(ledger_id)
            if status is None:
                raise TargetTagError(404, "ledger entry not found")
            if status != 0:
                raise TargetTagError(409, "inactive Ledger is read-only", code="LEDGER_INACTIVE")
            current = self.mapper.assignment(ledger_id)
            if current["updated_time"] != payload.expected_updated_time:
                raise TargetTagError(409, "tag assignment changed; reload before writing", code="TAG_STATE_CHANGED")
            state, tag_ids = self.projection.assignment(payload.tag_state)
            tags_unchanged = current["tag_ids"] == tuple(sorted(tag_ids))
            changed_views = {
                view for view, value in state.items()
                if value != current["tag_state"].get(view, "unclassified")
            }
            if payload.view_names is None:
                # Legacy full-state callers: only changed Views, or all Views
                # for an explicit same-state assignment.
                touched_views = changed_views or set(state)
            else:
                touched_views = set(payload.view_names)
                if len(touched_views) != len(payload.view_names):
                    raise ValueError("view_names cannot contain duplicates")
                if not touched_views.issubset(state):
                    raise ValueError("view_names must identify active views")
                if not changed_views.issubset(touched_views):
                    raise ValueError("tag values outside view_names cannot change")
            touched_tag_ids = {
                tag_id for view, tag_id in zip(state, tag_ids)
                if view in touched_views
            }
            updated_time = self._next_update_time(current["updated_time"])
            source_changed = self.requests.supersede_by_manual_assignment(
                ledger_id, touched_tag_ids, now=updated_time,
            )
            if tags_unchanged and not source_changed:
                self.mapper.commit()
                return TargetTagAssignmentRead(
                    ledger_id=ledger_id,
                    tag_state=state,
                    updated_time=current["updated_time"],
                )
            self.mapper.replace(
                ledger_id, list(tag_ids), updated_time,
                previous_tag_ids=current["tag_ids"],
                touched_tag_ids=touched_tag_ids,
            )
            self.mapper.commit()
            return TargetTagAssignmentRead(
                ledger_id=ledger_id,
                tag_state=state,
                updated_time=updated_time,
            )
        except TargetTagError:
            self.mapper.rollback()
            raise
        except (KeyError, ValueError) as error:
            self.mapper.rollback()
            raise TargetTagError(422, str(error), code="TAG_STATE_INVALID") from error
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetTagError(409, "tag assignment write conflict; retry") from error
        except Exception:
            self.mapper.rollback()
            raise

    @staticmethod
    def _next_update_time(previous: datetime | None) -> datetime:
        if previous is None:
            return utc_now()
        return max(utc_now(), previous + timedelta(microseconds=1))
