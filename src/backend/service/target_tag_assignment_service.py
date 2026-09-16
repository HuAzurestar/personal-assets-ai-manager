from __future__ import annotations

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.error import TargetTagError
from backend.mapper.target_tag_assignment_mapper import TargetTagAssignmentMapper
from backend.schema.target_tag import TargetTagAssignmentRead, TargetTagAssignmentRequest
from backend.service.target_tag_projection_service import TargetTagProjectionService


class TargetTagAssignmentService:
    """Replace the effective Tag state owned directly by one active Ledger."""

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
            if not self.mapper.active_ledger_exists(ledger_id):
                raise TargetTagError(404, "ledger entry not found")
            state, tag_ids = self.projection.assignment(payload.tag_state)
            self.mapper.replace(ledger_id, list(tag_ids))
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
