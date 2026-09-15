"""Legacy financial review HTTP adapter kept during API migration."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.target_review import (
    TargetReviewCaseListResponse,
    TargetReviewCasePageResponse,
    TargetReviewCaseResponse,
    TargetReviewCreateRequest,
    TargetReviewTransitionRequest,
    TargetReviewUpdateRequest,
)
from backend.service.target_review_service import TargetReviewService


router = APIRouter(
    prefix="/paam/review/v1",
    tags=["target-review"],
    route_class=DomainErrorRoute,
)


@router.post("/case/create", response_model=TargetReviewCaseResponse)
def create_case(
    payload: TargetReviewCreateRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(body=TargetReviewService(db).create(payload))


@router.post("/case/confirm/{case_id}", response_model=TargetReviewCaseResponse)
def confirm_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(body=TargetReviewService(db).confirm(case_id, payload))


@router.put("/case/update/{case_id}", response_model=TargetReviewCaseResponse)
def update_case(
    case_id: int,
    payload: TargetReviewUpdateRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(body=TargetReviewService(db).update(case_id, payload))


@router.post("/case/revoke/{case_id}", response_model=TargetReviewCaseResponse)
def revoke_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(body=TargetReviewService(db).revoke(case_id, payload))


@router.post("/case/restore/{case_id}", response_model=TargetReviewCaseResponse)
def restore_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(
        body=TargetReviewService(db).confirm(case_id, payload, restore=True)
    )


@router.post("/case/dismiss/{case_id}", response_model=TargetReviewCaseResponse)
def dismiss_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(body=TargetReviewService(db).dismiss(case_id, payload))


@router.post("/case/reopen/{case_id}", response_model=TargetReviewCaseResponse)
def reopen_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(
        body=TargetReviewService(db).dismiss(case_id, payload, reopen=True)
    )


@router.get("/case/detail/{case_id}", response_model=TargetReviewCaseResponse)
def case_detail(case_id: int, db: Session = Depends(get_db)):
    return TargetReviewCaseResponse(body=TargetReviewService(db).detail(case_id))


@router.get("/case/list", response_model=TargetReviewCaseListResponse)
def case_list(
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return TargetReviewCaseListResponse(body=TargetReviewService(db).list(limit))


@router.get("/case/page", response_model=TargetReviewCasePageResponse)
def case_page(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    status: str = Query(default="", pattern="^(|PENDING|CONFIRMED|REJECTED|REVOKED)$"),
    review_type: str = Query(
        default="",
        pattern="^(|CLASSIFICATION|AA|LOAN_BORROW|LOAN_LEND|REFUND|TRANSFER|FX_EXCHANGE|DUPLICATE|TAG|ACCOUNT|FACT_CONFLICT)$",
    ),
    db: Session = Depends(get_db),
):
    return TargetReviewCasePageResponse(
        body=TargetReviewService(db).page(page, page_size, status, review_type)
    )
