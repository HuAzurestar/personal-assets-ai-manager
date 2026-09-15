"""Import Fact conflict review HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.target_review import (
    TargetFactConflictResolveRequest,
    TargetReviewCaseResponse,
    TargetReviewTransitionRequest,
)
from backend.service.target_fact_conflict_service import TargetFactConflictService


router = APIRouter(
    prefix="/paam/import/v1",
    tags=["import"],
    route_class=DomainErrorRoute,
)


@router.post(
    "/fact-conflict/{conflict_id}/resolve",
    response_model=TargetReviewCaseResponse,
)
def resolve_conflict(
    conflict_id: int,
    payload: TargetFactConflictResolveRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(
        body=TargetFactConflictService(db).resolve(conflict_id, payload)
    )


@router.post(
    "/fact-conflict/{conflict_id}/dismiss",
    response_model=TargetReviewCaseResponse,
)
def dismiss_conflict(
    conflict_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(
        body=TargetFactConflictService(db).dismiss(conflict_id, payload)
    )


@router.post(
    "/fact-conflict/{conflict_id}/reopen",
    response_model=TargetReviewCaseResponse,
)
def reopen_conflict(
    conflict_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(
        body=TargetFactConflictService(db).reopen(conflict_id, payload)
    )
