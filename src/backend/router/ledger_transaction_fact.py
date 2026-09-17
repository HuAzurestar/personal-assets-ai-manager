"""Ledger-module Transaction Fact PO inspection HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from backend.router.dependency import get_db, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.list_query import iter_filter_fields, parse_list_request
from backend.schema.response import ResponseWarning
from backend.schema.transaction_fact import (
    TransactionFactDetailResponse,
    TransactionFactListRequest,
    TransactionFactListResponse,
)
from backend.service.transaction_fact_service import TransactionFactService


router = APIRouter(
    prefix="/paam/ledger/v1",
    tags=["ledger-transaction-fact"],
    route_class=DomainErrorRoute,
)


@router.get(
    "/transaction_fact/list",
    response_model=TransactionFactListResponse,
    response_model_exclude_none=True,
)
def transaction_fact_list(
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
        TransactionFactListRequest,
        page_index=page_index,
        page_size=page_size,
        query=query,
        filter=filter,
        sorter=sorter,
    )
    warnings: list[ResponseWarning] = []
    if request.sorter and request.sorter[0].key == "amount":
        equality_fields = {
            expression.key
            for expression in iter_filter_fields(request.filter)
            if expression.op == "="
        }
        if "currency_code" not in equality_fields:
            warnings.append(ResponseWarning(
                code="LIST_AMOUNT_SORT_GROUPED",
                message="Amounts are grouped by currency and scale before sorting",
                details={
                    "order": [
                        "currency_code asc",
                        f"amount {request.sorter[0].direction}",
                        f"id {request.sorter[0].direction}",
                    ],
                },
            ))
    return TransactionFactListResponse(
        status=200,
        message="ok",
        body=TransactionFactService(db).page(request=request),
        warnings=warnings,
    )


@router.get(
    "/transaction_fact/{fact_id}",
    response_model=TransactionFactDetailResponse,
)
def transaction_fact_detail(
    fact_id: int,
    db: Session = Depends(get_db),
):
    return TransactionFactDetailResponse(
        status=200,
        message="ok",
        body=TransactionFactService(db).detail(fact_id),
    )
