"""Ledger Review candidate query HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.list_query import parse_query_object
from backend.schema.target_review import (
    TargetReviewCandidateFilter,
    TargetReviewCandidatePageResponse,
    TargetReviewCandidateSorter,
)
from backend.service.target_economic_service import TargetEconomicService


router = APIRouter(
    prefix="/paam/ledger/v1",
    tags=["ledger-review-candidate"],
    route_class=DomainErrorRoute,
)


@router.get("/review_candidate/list", response_model=TargetReviewCandidatePageResponse)
def review_candidate_list(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
    filter: str = Query(default="{}"),
    sorter: str = Query(default='{"field":"occurred_time","order":"desc"}'),
    db: Session = Depends(get_db),
):
    filter_value = parse_query_object(filter, TargetReviewCandidateFilter, "filter")
    sorter_value = parse_query_object(sorter, TargetReviewCandidateSorter, "sorter")
    return TargetReviewCandidatePageResponse(
        message="Ledger review candidates listed",
        body=TargetEconomicService(db).review_candidate_page(
            page,
            page_size,
            q.strip(),
            filter_value,
            sorter_value,
        ),
    )
