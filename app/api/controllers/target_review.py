from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.target_deps import get_target_db
from app.schemas.target_review import (
    TargetAccountSetRequest,
    TargetFactConflictResolveRequest,
    TargetReviewCaseListResponse,
    TargetReviewCasePageResponse,
    TargetReviewCaseResponse,
    TargetReviewCreateRequest,
    TargetReviewTransitionRequest,
    TargetReviewUpdateRequest,
)
from app.services.target_account_service import TargetAccountService
from app.services.target_fact_conflict_service import TargetFactConflictService
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
    db: Session = Depends(get_target_db),
):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetReviewService(db).create(payload)
    ))


@router.post("/case/confirm/{case_id}", response_model=TargetReviewCaseResponse)
def confirm_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_target_db),
):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetReviewService(db).confirm(case_id, payload)
    ))


@router.put("/case/update/{case_id}", response_model=TargetReviewCaseResponse)
def update_case(
    case_id: int,
    payload: TargetReviewUpdateRequest,
    db: Session = Depends(get_target_db),
):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetReviewService(db).update(case_id, payload)
    ))


@router.post("/case/revoke/{case_id}", response_model=TargetReviewCaseResponse)
def revoke_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_target_db),
):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetReviewService(db).revoke(case_id, payload)
    ))


@router.post("/case/restore/{case_id}", response_model=TargetReviewCaseResponse)
def restore_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_target_db),
):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetReviewService(db).confirm(case_id, payload, restore=True)
    ))


@router.get("/case/detail/{case_id}", response_model=TargetReviewCaseResponse)
def case_detail(case_id: int, db: Session = Depends(get_target_db)):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetReviewService(db).detail(case_id)
    ))


@router.get("/case/list", response_model=TargetReviewCaseListResponse)
def case_list(
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_target_db),
):
    return TargetReviewCaseListResponse(body=_run(
        lambda: TargetReviewService(db).list(limit)
    ))


@router.get("/case/page", response_model=TargetReviewCasePageResponse)
def case_page(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    status: str = Query(default="", pattern="^(|PENDING|CONFIRMED|REJECTED|REVOKED)$"),
    db: Session = Depends(get_target_db),
):
    return TargetReviewCasePageResponse(body=_run(
        lambda: TargetReviewService(db).page(page, page_size, status)
    ))


@router.put("/account/set/{fact_id}", response_model=TargetReviewCaseResponse)
def set_account(
    fact_id: int,
    payload: TargetAccountSetRequest,
    db: Session = Depends(get_target_db),
):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetAccountService(db).set(fact_id, payload)
    ))


@router.post("/account/revoke/{case_id}", response_model=TargetReviewCaseResponse)
def revoke_account(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_target_db),
):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetAccountService(db).revoke(case_id, payload)
    ))


@router.post("/account/restore/{case_id}", response_model=TargetReviewCaseResponse)
def restore_account(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_target_db),
):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetAccountService(db).revoke(case_id, payload, restore=True)
    ))


@router.post("/fact-conflict/resolve/{case_id}", response_model=TargetReviewCaseResponse)
def resolve_fact_conflict(
    case_id: int,
    payload: TargetFactConflictResolveRequest,
    db: Session = Depends(get_target_db),
):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetFactConflictService(db).resolve(case_id, payload)
    ))


@router.post("/fact-conflict/dismiss/{case_id}", response_model=TargetReviewCaseResponse)
def dismiss_fact_conflict(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_target_db),
):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetFactConflictService(db).dismiss(case_id, payload)
    ))


@router.post("/fact-conflict/reopen/{case_id}", response_model=TargetReviewCaseResponse)
def reopen_fact_conflict(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_target_db),
):
    return TargetReviewCaseResponse(body=_run(
        lambda: TargetFactConflictService(db).reopen(case_id, payload)
    ))
