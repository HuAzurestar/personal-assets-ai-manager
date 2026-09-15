from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.error import TargetTagError
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
            target = self.mapper.target(ledger_id)
            if target is None:
                raise TargetTagError(404, "ledger entry not found")
            state, tag_ids = self.projection.assignment(payload.tag_state)
            current = self.mapper.state(ledger_id)
            if current == state:
                self.mapper.commit()
                return self._response(ledger_id, target.projection_version, state)
            if target.projection_version != payload.expected_projection_version:
                raise TargetTagError(
                    409, "ledger projection changed; reload before assigning tags"
                )
            self.mapper.replace(ledger_id, tag_ids)
            version = self.mapper.advance_projection(
                ledger_id,
                target.projection_version,
                datetime.now(),
            )
            self.mapper.commit()
            return self._response(ledger_id, version, state)
        except TargetTagError:
            self.mapper.rollback()
            raise
        except ValueError as error:
            self.mapper.rollback()
            raise TargetTagError(422, str(error)) from error
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetTagError(
                409, "tag assignment write conflict; retry from the latest projection"
            ) from error
        except Exception:
            self.mapper.rollback()
            raise

    @staticmethod
    def _response(
        ledger_id: int,
        version: int,
        state: dict[str, str],
    ) -> TargetTagAssignmentRead:
        return TargetTagAssignmentRead(
            ledger_id=ledger_id,
            projection_version=version,
            tag_state=state,
        )
