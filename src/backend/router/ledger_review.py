"""Production Fact-Review-Ledger allocation HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.list_query import parse_query_object
from backend.schema.review_case import (
    ReviewCaseFilter,
    ReviewCasePageResponse,
    ReviewCaseSorter,
)
from backend.schema.target_review import (
    TargetEconomicReviewCreateRequest,
    TargetEconomicReviewResponse,
    TargetEconomicReviewUpdateRequest,
    TargetReviewTransitionRequest,
)
from backend.service.target_economic_service import TargetEconomicService


router = APIRouter(
    prefix="/paam/ledger/v1",
    tags=["ledger-review"],
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


@router.post("/review/{review_id}/confirm", response_model=TargetEconomicReviewResponse)
def confirm_case(
    review_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewResponse(
        message="Ledger review confirmed",
        body=TargetEconomicService(db).confirm(review_id, payload)
    )


@router.put("/review/{review_id}", response_model=TargetEconomicReviewResponse)
def update_case(
    review_id: int,
    payload: TargetEconomicReviewUpdateRequest,
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewResponse(
        message="Ledger review updated",
        body=TargetEconomicService(db).update(review_id, payload)
    )


@router.post("/review/{review_id}/revoke", response_model=TargetEconomicReviewResponse)
def revoke_case(
    review_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewResponse(
        message="Ledger review revoked",
        body=TargetEconomicService(db).revoke(review_id, payload)
    )


@router.post("/review/{review_id}/restore", response_model=TargetEconomicReviewResponse)
def restore_case(
    review_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewResponse(
        message="Ledger review restored",
        body=TargetEconomicService(db).confirm(review_id, payload, restore=True)
    )


@router.get("/review/list", response_model=ReviewCasePageResponse)
def case_page(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: int | None = Query(default=None, ge=0, le=1),
    q: str = Query(default="", max_length=200),
    filter: str = Query(default="{}"),
    sorter: str = Query(default='{"field":"updated_time","order":"desc"}'),
    db: Session = Depends(get_db),
):
    filter_value = parse_query_object(filter, ReviewCaseFilter, "filter")
    sorter_value = parse_query_object(sorter, ReviewCaseSorter, "sorter")
    if status is not None and filter_value.status is None:
        filter_value = filter_value.model_copy(update={"status": status})
    return ReviewCasePageResponse(
        message="Ledger reviews listed",
        body=TargetEconomicService(db).page(
            page,
            page_size,
            q.strip(),
            filter_value,
            sorter_value,
        )
    )


@router.get("/review/{review_id}", response_model=TargetEconomicReviewResponse)
def case_detail(review_id: int, db: Session = Depends(get_db)):
    return TargetEconomicReviewResponse(
        message="Ledger review returned",
        body=TargetEconomicService(db).detail(review_id)
    )
