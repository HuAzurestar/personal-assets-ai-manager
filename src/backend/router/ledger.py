from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.schema.target_economic import (
    EconomicFlowDetailRead,
    EconomicFlowPageRead,
    EconomicPageQuery,
    EconomicSummaryQuery,
    EconomicSummaryRead,
)
from backend.service.target_economic_read_service import TargetEconomicReadService


router = APIRouter(prefix="/paam/economy/v1", tags=["economic-flow"])


@router.get("/flow/list", response_model=EconomicFlowPageRead)
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
        return TargetEconomicReadService(db).page(EconomicPageQuery(
            page=page,
            page_size=page_size,
            date_from=date_from,
            date_to=date_to,
            economic_type=tuple(economic_type),
            currency_code=tuple(code.upper() for code in currency_code),
            q=q.strip(),
        ))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/flow/detail/{economic_id}", response_model=EconomicFlowDetailRead)
def economic_flow_detail(economic_id: int, db: Session = Depends(get_db)):
    result = TargetEconomicReadService(db).detail(economic_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Economic flow not found")
    return result


@router.get("/summary", response_model=EconomicSummaryRead)
def economic_summary(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be before date_to")
    return TargetEconomicReadService(db).summary(EconomicSummaryQuery(
        date_from=date_from,
        date_to=date_to,
    ))
