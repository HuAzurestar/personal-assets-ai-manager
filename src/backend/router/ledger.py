from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.list_query import parse_query_object
from backend.schema.target_economic import (
    EconomicFlowFilter,
    EconomicFlowSorter,
    EconomicFlowDetailResponse,
    EconomicFlowPageResponse,
    EconomicPageQuery,
    EconomicSummaryQuery,
    EconomicSummaryResponse,
)
from backend.service.target_economic_read_service import TargetEconomicReadService


router = APIRouter(
    prefix="/paam/ledger/v1",
    tags=["ledger-flow"],
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
    page_size: int = Query(default=20, ge=1, le=100),
    date_from: date | None = None,
    date_to: date | None = None,
    economic_type: list[str] = Query(default=[]),
    currency_code: list[str] = Query(default=[]),
    q: str = Query(default="", max_length=200),
    filter: str = Query(default="{}"),
    sorter: str = Query(default='{"field":"occurred_time","order":"desc"}'),
    db: Session = Depends(get_db),
):
    filter_value = parse_query_object(filter, EconomicFlowFilter, "filter")
    sorter_value = parse_query_object(sorter, EconomicFlowSorter, "sorter")
    effective_date_from = filter_value.date_from or date_from
    effective_date_to = filter_value.date_to or date_to
    effective_types = filter_value.economic_type or economic_type
    effective_currencies = filter_value.currency_code or currency_code
    if effective_date_from and effective_date_to and effective_date_from > effective_date_to:
        raise HTTPException(status_code=422, detail="date_from must be before date_to")
    try:
        invalid = sorted(set(effective_types) - set(ECONOMIC_TYPE_IDS))
        if invalid:
            raise ValueError(f"unknown economic types: {invalid}")
        return EconomicFlowPageResponse(
            message="Ledger flows listed",
            body=TargetEconomicReadService(db).page(EconomicPageQuery(
                page=page,
                page_size=page_size,
                date_from=effective_date_from,
                date_to=effective_date_to,
                entry_type=tuple(ECONOMIC_TYPE_IDS[item] for item in effective_types),
                currency_code=tuple(code.upper() for code in effective_currencies),
                cash_direction={"IN": 1, "OUT": 2}.get(filter_value.cash_direction),
                account_code=filter_value.account_code or "",
                q=q.strip(),
                sort_field=sorter_value.field,
                sort_order=sorter_value.order,
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
