"""Ledger-facing Fact candidate HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.target_review import TargetFactAllocationCandidatePageResponse
from backend.service.target_economic_service import TargetEconomicService


router = APIRouter(
    prefix="/paam/ledger/v1",
    tags=["ledger-fact"],
    route_class=DomainErrorRoute,
)


@router.get("/fact/list", response_model=TargetFactAllocationCandidatePageResponse)
def fact_list(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return TargetFactAllocationCandidatePageResponse(
        body=TargetEconomicService(db).fact_candidate_page(page, page_size)
    )
