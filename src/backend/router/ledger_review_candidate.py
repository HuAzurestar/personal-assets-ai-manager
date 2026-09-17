"""Ledger Review candidate query HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from backend.router.dependency import get_db, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.list_query import parse_list_request
from backend.schema.target_review import (
    TargetReviewCandidateListRequest,
    TargetReviewCandidateListResponse,
)
from backend.service.target_economic_service import TargetEconomicService


router = APIRouter(
    prefix="/paam/ledger/v1",
    tags=["ledger-review-candidate"],
    route_class=DomainErrorRoute,
)


@router.get(
    "/review_candidate/list",
    response_model=TargetReviewCandidateListResponse,
    response_model_exclude_none=True,
)
def review_candidate_list(
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
        TargetReviewCandidateListRequest,
        page_index=page_index,
        page_size=page_size,
        query=query,
        filter=filter,
        sorter=sorter,
    )
    return TargetReviewCandidateListResponse(
        status=200,
        message="ok",
        body=TargetEconomicService(db).review_candidate_page(request=request),
    )
