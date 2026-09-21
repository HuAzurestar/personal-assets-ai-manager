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

    def assign(
        self,
        ledger_id: int,
        payload: TargetTagAssignmentRequest,
    ) -> TargetTagAssignmentRead:
        try:
            self.mapper.begin_write()
            current = self.mapper.ledger(ledger_id)
            if current is None:
                raise TargetTagError(404, "ledger entry not found")
            state, tag_ids = self.projection.assignment(payload.tag_state)
            if self.mapper.current_tag_ids(ledger_id) == tuple(sorted(tag_ids)):
                self.mapper.commit()
                return TargetTagAssignmentRead(ledger_id=ledger_id, tag_state=state)
            self.mapper.replace(ledger_id, list(tag_ids))
            if not self.mapper.touch(
                ledger_id,
                current["updated_time"],
                self._next_update_time(current["updated_time"]),
            ):
                raise TargetTagError(409, "ledger changed; reload before writing")
            self.mapper.commit()
            return TargetTagAssignmentRead(
                ledger_id=ledger_id,
                tag_state=state,
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
    def _next_update_time(previous: datetime) -> datetime:
        return max(utc_now(), previous + timedelta(milliseconds=1))
