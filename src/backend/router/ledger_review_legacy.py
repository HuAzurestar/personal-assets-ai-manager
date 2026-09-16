"""Legacy Review query HTTP adapter retained for active UI callers."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.target_review import (
    TargetReviewCasePageResponse,
    TargetReviewCaseResponse,
)
from backend.service.target_review_service import TargetReviewService


router = APIRouter(
    prefix="/paam/review/v1",
    tags=["target-review"],
    route_class=DomainErrorRoute,
)


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


@router.get("/case/detail/{case_id}", response_model=TargetReviewCaseResponse)
def case_detail(case_id: int, db: Session = Depends(get_db)):
    return TargetReviewCaseResponse(body=TargetReviewService(db).detail(case_id))
