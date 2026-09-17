from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.router.dependency import get_db, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.list_query import iter_filter_fields, parse_list_request
from backend.schema.response import ResponseWarning
from backend.schema.target_economic import (
    EconomicFlowDetailResponse,
    EconomicFlowListRequest,
    EconomicFlowListResponse,
    EconomicSummaryQuery,
    EconomicSummaryResponse,
)
from backend.service.target_economic_read_service import TargetEconomicReadService


router = APIRouter(
    prefix="/paam/ledger/v1",
    tags=["ledger-flow"],
    route_class=DomainErrorRoute,
)

@router.get(
    "/flow/list",
    response_model=EconomicFlowListResponse,
    response_model_exclude_none=True,
)
def list_economic_flows(
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
        EconomicFlowListRequest,
        page_index=page_index,
        page_size=page_size,
        query=query,
        filter=filter,
        sorter=sorter,
    )
    warnings: list[ResponseWarning] = []
    if request.sorter and request.sorter[0].key == "amount_value":
        equality_fields = {
            expression.key
            for expression in iter_filter_fields(request.filter)
            if expression.op == "="
        }
        if not {"currency_code", "amount_scale"}.issubset(equality_fields):
            warnings.append(ResponseWarning(
                code="LIST_AMOUNT_SORT_GROUPED",
                message="Amounts are grouped by currency and scale before sorting",
                details={
                    "order": [
                        "currency_code asc",
                        "amount_scale asc",
                        f"amount_value {request.sorter[0].direction}",
                        f"id {request.sorter[0].direction}",
                    ],
                },
            ))
    return EconomicFlowListResponse(
        status=200,
        message="ok",
        body=TargetEconomicReadService(db).page(request),
        warnings=warnings,
    )


@router.get("/flow/summary", response_model=EconomicSummaryResponse)
def economic_summary(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be before date_to")
    return EconomicSummaryResponse(
        status=200,
        message="ok",
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
        status=200,
        message="ok",
        body=result,
    )
