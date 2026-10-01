"""Ledger Review candidate query HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from backend.router.dependency import get_db, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.list_query import parse_list_request
from backend.schema.candidate import (
    CandidateListRequest,
    TargetReviewCandidateListResponse,
)
from backend.service.candidate_service import CandidateService


router = APIRouter(
    prefix="/paam/ledger/v1",
    tags=["ledger-review-candidate"],
    route_class=DomainErrorRoute,
)


@router.get(
    "/review_candidate/list",
    response_model=TargetReviewCandidateListResponse,
    deprecated=True,
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
        CandidateListRequest,
        page_index=page_index,
        page_size=page_size,
        query=query,
        filter=filter,
        sorter=sorter,
    )
    return TargetReviewCandidateListResponse(
        status=200,
        message="ok",
        body=CandidateService(db).page(request),
    )
