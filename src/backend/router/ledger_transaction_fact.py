"""Ledger-module Transaction Fact PO inspection HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.list_query import parse_query_object
from backend.schema.transaction_fact import (
    TransactionFactDetailResponse,
    TransactionFactFilter,
    TransactionFactPageResponse,
    TransactionFactSorter,
)
from backend.service.transaction_fact_service import TransactionFactService


router = APIRouter(
    prefix="/paam/ledger/v1",
    tags=["ledger-transaction-fact"],
    route_class=DomainErrorRoute,
)


@router.get(
    "/transaction_fact/list",
    response_model=TransactionFactPageResponse,
)
def transaction_fact_list(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
    filter: str = Query(default="{}"),
    sorter: str = Query(default='{"field":"occurred_time","order":"desc"}'),
    db: Session = Depends(get_db),
):
    filter_value = parse_query_object(filter, TransactionFactFilter, "filter")
    sorter_value = parse_query_object(sorter, TransactionFactSorter, "sorter")
    return TransactionFactPageResponse(
        message="Transaction facts listed",
        body=TransactionFactService(db).page(
            page=page,
            page_size=page_size,
            q=q.strip(),
            filter_value=filter_value,
            sorter=sorter_value,
        ),
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
        message="Transaction fact loaded",
        body=TransactionFactService(db).detail(fact_id),
    )
