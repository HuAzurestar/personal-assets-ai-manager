from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.target_tag import (
    TargetTagAssignmentRequest,
    TargetTagAssignmentResponse,
)
from backend.service.target_tag_assignment_service import TargetTagAssignmentService


router = APIRouter(
    prefix="/paam/tag/v1",
    tags=["tag"],
    route_class=DomainErrorRoute,
)


@router.put("/assignment/{ledger_id}", response_model=TargetTagAssignmentResponse)
def set_assignment(
    ledger_id: int,
    payload: TargetTagAssignmentRequest,
    db: Session = Depends(get_db),
):
    return TargetTagAssignmentResponse(
        message="Ledger tag assignment updated",
        body=TargetTagAssignmentService(db).assign(ledger_id, payload)
    )
