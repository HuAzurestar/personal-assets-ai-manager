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
    TargetEconomicReviewUpdateRequest,
    TargetFactAllocationCandidateResponse,
    TargetReviewTransitionRequest,
)
from backend.service.target_economic_service import TargetEconomicService


router = APIRouter(
    prefix="/paam/review/v2",
    tags=["economic-review"],
    route_class=DomainErrorRoute,
)


@router.post("/case/create", response_model=TargetEconomicReviewResponse)
def create_case(
    payload: TargetEconomicReviewCreateRequest,
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewResponse(body=TargetEconomicService(db).create(payload))


@router.post("/case/confirm/{case_id}", response_model=TargetEconomicReviewResponse)
def confirm_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewResponse(
        body=TargetEconomicService(db).confirm(case_id, payload)
    )


@router.put("/case/update/{case_id}", response_model=TargetEconomicReviewResponse)
def update_case(
    case_id: int,
    payload: TargetEconomicReviewUpdateRequest,
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewResponse(
        body=TargetEconomicService(db).update(case_id, payload)
    )


@router.post("/case/revoke/{case_id}", response_model=TargetEconomicReviewResponse)
def revoke_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewResponse(
        body=TargetEconomicService(db).revoke(case_id, payload)
    )


@router.post("/case/restore/{case_id}", response_model=TargetEconomicReviewResponse)
def restore_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewResponse(
        body=TargetEconomicService(db).confirm(case_id, payload, restore=True)
    )


@router.get("/case/detail/{case_id}", response_model=TargetEconomicReviewResponse)
def case_detail(case_id: int, db: Session = Depends(get_db)):
    return TargetEconomicReviewResponse(body=TargetEconomicService(db).detail(case_id))


@router.get("/case/page", response_model=TargetEconomicReviewPageResponse)
def case_page(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    status: str = Query(default="", pattern="^(|PENDING|CONFIRMED|REVOKED)$"),
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewPageResponse(
        body=TargetEconomicService(db).page(page, page_size, status)
    )


@router.get("/fact/candidates", response_model=TargetFactAllocationCandidateResponse)
def fact_candidates(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return TargetFactAllocationCandidateResponse(
        body=TargetEconomicService(db).fact_candidates(limit)
    )
