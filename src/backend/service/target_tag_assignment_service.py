from __future__ import annotations

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.mapper.target_tag_assignment_mapper import TargetTagAssignmentMapper
from backend.schema.target_tag import TargetTagAssignmentRead, TargetTagAssignmentRequest
from backend.service.target_tag_projection_service import TargetTagProjectionService
from backend.service.target_tag_service import TargetTagError


class TargetTagAssignmentService:
    """Assign tags directly to one active LedgerEntry."""

    def __init__(self, db: Session):
        self.mapper = TargetTagAssignmentMapper(db)
        self.projection = TargetTagProjectionService(db)

    def assign(self, ledger_id: int, payload: TargetTagAssignmentRequest) -> TargetTagAssignmentRead:
        try:
            self.mapper.begin_write()
            if not self.mapper.active_ledger_exists(ledger_id):
                raise TargetTagError(404, "ledger entry not found")
            state = self.projection.validate_complete(payload.tag_state)
            self.mapper.replace(ledger_id, self.mapper.tag_ids(state))
            self.mapper.commit()
            return TargetTagAssignmentRead(
                ledger_id=ledger_id,
                version=0,
                tag_state=state,
                review_case_ids=[],
            )
        except TargetTagError:
            self.mapper.rollback()
            raise
        except ValueError as error:
            self.mapper.rollback()
            raise TargetTagError(422, str(error)) from error
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetTagError(409, "tag assignment write conflict; retry") from error
