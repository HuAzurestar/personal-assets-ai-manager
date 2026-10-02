"""Production Fact-Review-Ledger allocation HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.orm import Session

from backend.router.dependency import get_db, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.list_query import parse_list_request
from backend.schema.review_query import (ReviewListRequest, ReviewCaseListResponse, ReviewSearchRequest,
    ReviewSearchResponse, ReviewRelationRequest, ReviewAllocationListResponse, ReviewFlowListResponse,
    ReviewLegListResponse, ReviewPositionAllocationListResponse, ReviewPositionListResponse)
from backend.router.bounded_query import bounded_list_dependency, bounded_search_dependency
from backend.service.review_read_service import ReviewReadService
from backend.service.target_economic_service import TargetEconomicService
from backend.schema.review_command import ReviewChangeInput, ReviewCommandInput
from backend.schema.review_read import ReviewCommandResponse, ReviewPreviewResponse, ReviewReadResponse
from backend.service.review_command_service import ReviewCommandService


router = APIRouter(
    prefix="/paam/ledger/v1",
    tags=["ledger-review"],
    route_class=DomainErrorRoute,
)


@router.post("/review", deprecated=True)
def create_case(
    payload: dict,
    db: Session = Depends(get_db),
):
    return TargetEconomicService(db).create(payload)


@router.post("/review/{review_id}/revoke", deprecated=True)
def revoke_case(
    review_id: int,
    payload: dict,
    db: Session = Depends(get_db),
):
    return TargetEconomicService(db).revoke(review_id, payload)


@router.post("/review/{review_id}/restore", deprecated=True)
def restore_case(
    review_id: int,
    payload: dict,
    db: Session = Depends(get_db),
):
    return TargetEconomicService(db).restore(review_id, payload)


@router.get(
    "/review/list",
    response_model=ReviewCaseListResponse,
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
        ReviewListRequest,
        page_index=page_index,
        page_size=page_size,
        query=query,
        filter=filter,
        sorter=sorter,
    )
    return ReviewCaseListResponse(
        status=200,
        message="ok",
        body=ReviewReadService(db).page(request),
    )


@router.post("/review/preview", response_model=ReviewPreviewResponse)
def preview_review(payload: ReviewChangeInput, db: Session = Depends(get_db)):
    return ReviewPreviewResponse(status=200, message="ok", body=ReviewCommandService(db).preview(payload))


@router.post("/review/command", response_model=ReviewCommandResponse)
def command_review(payload: ReviewCommandInput, db: Session = Depends(get_db)):
    return ReviewCommandResponse(status=200, message="ok", body=ReviewCommandService(db).command(payload))


@router.get("/review/search", response_model=ReviewSearchResponse)
def search_reviews(request: ReviewSearchRequest = Depends(bounded_search_dependency(ReviewSearchRequest)), db: Session = Depends(get_db)):
    return ReviewSearchResponse(status=200, message="ok", body=ReviewReadService(db).search(request))


@router.get("/review/{review_id}/allocation/list", response_model=ReviewAllocationListResponse)
def review_allocations(review_id: int = Path(ge=1, le=2**63 - 1),
    request: ReviewRelationRequest = Depends(bounded_list_dependency(ReviewRelationRequest)), db: Session = Depends(get_db)):
    return ReviewAllocationListResponse(status=200, message="ok", body=ReviewReadService(db).relation_page(review_id, "allocation", request))


@router.get("/review/{review_id}/flow/list", response_model=ReviewFlowListResponse)
def review_flows(review_id: int = Path(ge=1, le=2**63 - 1),
    request: ReviewRelationRequest = Depends(bounded_list_dependency(ReviewRelationRequest)), db: Session = Depends(get_db)):
    return ReviewFlowListResponse(status=200, message="ok", body=ReviewReadService(db).relation_page(review_id, "flow", request))


@router.get("/review/{review_id}/position_leg/list", response_model=ReviewLegListResponse)
def review_legs(review_id: int = Path(ge=1, le=2**63 - 1),
    request: ReviewRelationRequest = Depends(bounded_list_dependency(ReviewRelationRequest)), db: Session = Depends(get_db)):
    return ReviewLegListResponse(status=200, message="ok", body=ReviewReadService(db).relation_page(review_id, "position_leg", request))


@router.get("/review/{review_id}/position_allocation/list", response_model=ReviewPositionAllocationListResponse)
def review_position_allocations(review_id: int = Path(ge=1, le=2**63 - 1),
    request: ReviewRelationRequest = Depends(bounded_list_dependency(ReviewRelationRequest)), db: Session = Depends(get_db)):
    return ReviewPositionAllocationListResponse(status=200, message="ok", body=ReviewReadService(db).relation_page(review_id, "position_allocation", request))


@router.get("/review/{review_id}/position/list", response_model=ReviewPositionListResponse)
def review_positions(review_id: int = Path(ge=1, le=2**63 - 1),
    request: ReviewRelationRequest = Depends(bounded_list_dependency(ReviewRelationRequest)), db: Session = Depends(get_db)):
    return ReviewPositionListResponse(status=200, message="ok", body=ReviewReadService(db).relation_page(review_id, "position", request))


@router.get("/review/{review_id}", response_model=ReviewReadResponse)
def case_detail(review_id: int = Path(ge=1, le=2**63 - 1), db: Session = Depends(get_db)):
    return ReviewReadResponse(
        status=200, message="ok",
        body=ReviewReadService(db).detail(review_id)
    )
