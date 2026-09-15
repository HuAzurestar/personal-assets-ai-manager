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
    prefix="/paam/review/v1",
    tags=["target-review"],
    route_class=DomainErrorRoute,
)


@router.post("/fact-conflict/resolve/{case_id}", response_model=TargetReviewCaseResponse)
def resolve_conflict(
    case_id: int,
    payload: TargetFactConflictResolveRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(
        body=TargetFactConflictService(db).resolve(case_id, payload)
    )


@router.post("/fact-conflict/dismiss/{case_id}", response_model=TargetReviewCaseResponse)
def dismiss_conflict(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(
        body=TargetFactConflictService(db).dismiss(case_id, payload)
    )


@router.post("/fact-conflict/reopen/{case_id}", response_model=TargetReviewCaseResponse)
def reopen_conflict(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(
        body=TargetFactConflictService(db).reopen(case_id, payload)
    )
