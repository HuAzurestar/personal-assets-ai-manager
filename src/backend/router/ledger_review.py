"""Production Fact-Review-Ledger allocation HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.target_review import (
    TargetEconomicReviewCreateRequest,
    TargetEconomicReviewPageResponse,
    TargetEconomicReviewResponse,
    TargetEconomicReviewTransitionRequest,
)
from backend.service.target_economic_service import TargetEconomicService


router = APIRouter(
    prefix="/paam/ledger/v1",
    tags=["economic-review"],
    route_class=DomainErrorRoute,
)


@router.post("/review", response_model=TargetEconomicReviewResponse)
def create_case(
    payload: TargetEconomicReviewCreateRequest,
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewResponse(
        message="Ledger review created",
        body=TargetEconomicService(db).create(payload),
    )


@router.post("/review/{review_id}/revoke", response_model=TargetEconomicReviewResponse)
def revoke_case(
    review_id: int,
    payload: TargetEconomicReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewResponse(
        message="Ledger review revoked",
        body=TargetEconomicService(db).revoke(review_id, payload)
    )


@router.post("/review/{review_id}/restore", response_model=TargetEconomicReviewResponse)
def restore_case(
    review_id: int,
    payload: TargetEconomicReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewResponse(
        message="Ledger review restored",
        body=TargetEconomicService(db).restore(review_id, payload)
    )


@router.get("/review/list", response_model=TargetEconomicReviewPageResponse)
def case_page(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    status: int | None = Query(default=None, ge=0, le=1),
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewPageResponse(
        message="Ledger reviews listed",
        body=TargetEconomicService(db).page(page, page_size, status)
    )


@router.get("/review/{review_id}", response_model=TargetEconomicReviewResponse)
def case_detail(review_id: int, db: Session = Depends(get_db)):
    return TargetEconomicReviewResponse(
        message="Ledger review returned",
        body=TargetEconomicService(db).detail(review_id)
    )
