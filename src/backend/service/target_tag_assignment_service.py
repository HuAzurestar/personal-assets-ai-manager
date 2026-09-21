from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.error import TargetTagError
from backend.entity.base import utc_now
from backend.mapper.target_tag_assignment_mapper import TargetTagAssignmentMapper
from backend.schema.target_tag import TargetTagAssignmentRead, TargetTagAssignmentRequest
from backend.service.target_tag_projection_service import TargetTagProjectionService


class TargetTagAssignmentService:
    """Replace the effective Tag state owned directly by one Ledger."""

    def __init__(self, db: Session):
        self.mapper = TargetTagAssignmentMapper(db)
        self.projection = TargetTagProjectionService(db)

    def get(self, ledger_id: int) -> TargetTagAssignmentRead:
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
            if not self.mapper.ledger_exists(ledger_id):
                raise TargetTagError(404, "ledger entry not found")
            current = self.mapper.assignment(ledger_id)
            if current["updated_time"] != payload.expected_updated_time:
                raise TargetTagError(409, "tag assignment changed; reload before writing")
            state, tag_ids = self.projection.assignment(payload.tag_state)
            if current["tag_ids"] == tuple(sorted(tag_ids)):
                self.mapper.commit()
                return TargetTagAssignmentRead(
                    ledger_id=ledger_id,
                    tag_state=state,
                    updated_time=current["updated_time"],
                )
            updated_time = self._next_update_time(current["updated_time"])
            self.mapper.replace(ledger_id, list(tag_ids), updated_time)
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
            raise TargetTagError(422, str(error)) from error
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
