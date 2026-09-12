from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.target_review import (
    TargetReviewCaseListResponse,
    TargetReviewCaseResponse,
    TargetReviewCreateRequest,
    TargetReviewTransitionRequest,
    TargetReviewUpdateRequest,
)
from app.services.target_review_service import TargetReviewError, TargetReviewService


router = APIRouter(prefix="/paam/review/v1", tags=["target-review"])


def _run(operation: Callable[[], object]):
    try:
        return operation()
    except TargetReviewError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error


@router.post("/case/create", response_model=TargetReviewCaseResponse)
def create_case(
    payload: TargetReviewCreateRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetReviewService(db).create(payload)
    ))


@router.post("/case/confirm/{case_id}", response_model=TargetReviewCaseResponse)
def confirm_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetReviewService(db).confirm(case_id, payload)
    ))


@router.put("/case/update/{case_id}", response_model=TargetReviewCaseResponse)
def update_case(
    case_id: int,
    payload: TargetReviewUpdateRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetReviewService(db).update(case_id, payload)
    ))


@router.post("/case/revoke/{case_id}", response_model=TargetReviewCaseResponse)
def revoke_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetReviewService(db).revoke(case_id, payload)
    ))


@router.post("/case/restore/{case_id}", response_model=TargetReviewCaseResponse)
def restore_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetReviewService(db).confirm(case_id, payload, restore=True)
    ))


@router.get("/case/detail/{case_id}", response_model=TargetReviewCaseResponse)
def case_detail(case_id: int, db: Session = Depends(get_db)):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetReviewService(db).detail(case_id)
    ))


@router.get("/case/list", response_model=TargetReviewCaseListResponse)
def case_list(
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return TargetReviewCaseListResponse(body=_run(
        lambda: TargetReviewService(db).list(limit)
    ))
