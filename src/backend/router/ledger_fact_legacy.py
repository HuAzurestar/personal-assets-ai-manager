"""Temporary limit-based Fact candidate compatibility route."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.target_review import TargetFactAllocationCandidateResponse
from backend.service.target_economic_service import TargetEconomicService


router = APIRouter(
    prefix="/paam/review/v2",
    tags=["economic-review"],
    route_class=DomainErrorRoute,
)


@router.get("/fact/candidates", response_model=TargetFactAllocationCandidateResponse)
def fact_candidates(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return TargetFactAllocationCandidateResponse(
        body=TargetEconomicService(db).fact_candidates(limit)
    )
