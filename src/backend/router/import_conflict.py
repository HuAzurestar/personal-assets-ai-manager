"""Import Fact conflict review HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.intake import ImportFactConflictResponse
from backend.schema.target_review import (
    TargetFactConflictResolveRequest,
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
    response_model=ImportFactConflictResponse,
)
def resolve_conflict(
    conflict_id: int,
    payload: TargetFactConflictResolveRequest,
    db: Session = Depends(get_db),
):
    return ImportFactConflictResponse(
        message="Fact conflict resolved",
        body=TargetFactConflictService(db).resolve(conflict_id, payload)
    )


@router.post(
    "/fact-conflict/{conflict_id}/dismiss",
    response_model=ImportFactConflictResponse,
)
def dismiss_conflict(
    conflict_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return ImportFactConflictResponse(
        message="Fact conflict dismissed",
        body=TargetFactConflictService(db).dismiss(conflict_id, payload)
    )


@router.post(
    "/fact-conflict/{conflict_id}/reopen",
    response_model=ImportFactConflictResponse,
)
def reopen_conflict(
    conflict_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return ImportFactConflictResponse(
        message="Fact conflict reopened",
        body=TargetFactConflictService(db).reopen(conflict_id, payload)
    )
