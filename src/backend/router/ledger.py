from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.target_economic import (
    EconomicFlowDetailResponse,
    EconomicFlowPageResponse,
    EconomicPageQuery,
    EconomicSummaryQuery,
    EconomicSummaryResponse,
)
from backend.service.target_economic_read_service import TargetEconomicReadService


router = APIRouter(
    prefix="/paam/ledger/v1",
    tags=["economic-flow"],
    route_class=DomainErrorRoute,
)

ECONOMIC_TYPE_IDS = {
    "INCOME_AND_EXPENSE": 0,
    "INTERNAL_TRANSFER": 1,
    "ASSET_AND_LIABILITY": 2,
}


@router.get("/flow/list", response_model=EconomicFlowPageResponse)
def list_economic_flows(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    date_from: date | None = None,
    date_to: date | None = None,
    economic_type: list[str] = Query(default=[]),
    currency_code: list[str] = Query(default=[]),
    q: str = Query(default="", max_length=200),
    db: Session = Depends(get_db),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be before date_to")
    try:
        invalid = sorted(set(economic_type) - set(ECONOMIC_TYPE_IDS))
        if invalid:
            raise ValueError(f"unknown economic types: {invalid}")
        return EconomicFlowPageResponse(
            message="Ledger flows listed",
            body=TargetEconomicReadService(db).page(EconomicPageQuery(
                page=page,
                page_size=page_size,
                date_from=date_from,
                date_to=date_to,
                entry_type=tuple(ECONOMIC_TYPE_IDS[item] for item in economic_type),
                currency_code=tuple(code.upper() for code in currency_code),
                q=q.strip(),
            )),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/flow/summary", response_model=EconomicSummaryResponse)
def economic_summary(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be before date_to")
    return EconomicSummaryResponse(
        message="Ledger flow summary returned",
        body=TargetEconomicReadService(db).summary(EconomicSummaryQuery(
            date_from=date_from,
            date_to=date_to,
        )),
    )


@router.get("/flow/{ledger_id}", response_model=EconomicFlowDetailResponse)
def economic_flow_detail(ledger_id: int, db: Session = Depends(get_db)):
    result = TargetEconomicReadService(db).detail(ledger_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Economic flow not found")
    return EconomicFlowDetailResponse(
        message="Ledger flow returned",
        body=result,
    )
