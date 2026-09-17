"""Production Fact-Review-Ledger allocation HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from backend.router.dependency import get_db, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.list_query import parse_list_request
from backend.schema.target_review import (
    TargetEconomicReviewCreateRequest,
    TargetEconomicReviewListRequest,
    TargetEconomicReviewListResponse,
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


@router.get(
    "/review/list",
    response_model=TargetEconomicReviewListResponse,
    response_model_exclude_none=True,
)
def case_page(
    http_request: Request,
    page_index: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    query: str | None = Query(default=None),
    filter: str | None = Query(default=None),
    sorter: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    validate_query_parameter_names(
        http_request,
        {"page_index", "page_size", "query", "filter", "sorter"},
    )
    request = parse_list_request(
        TargetEconomicReviewListRequest,
        page_index=page_index,
        page_size=page_size,
        query=query,
        filter=filter,
        sorter=sorter,
    )
    return TargetEconomicReviewListResponse(
        status=200,
        message="ok",
        body=TargetEconomicService(db).page(request=request),
    )


@router.get("/review/{review_id}", response_model=TargetEconomicReviewResponse)
def case_detail(review_id: int, db: Session = Depends(get_db)):
    return TargetEconomicReviewResponse(
        message="Ledger review returned",
        body=TargetEconomicService(db).detail(review_id)
    )
