from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.router.dependency import get_db, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.list_query import parse_list_request
from backend.schema.ledger_entry import (
    LedgerEntryDetailResponse,
    LedgerEntryListRequest,
    LedgerEntryListResponse,
    LedgerEntrySummaryQuery,
    LedgerEntrySummaryResponse,
)
from backend.service.ledger_entry_service import LedgerEntryService


router = APIRouter(
    prefix="/paam/ledger/v1",
    tags=["ledger-flow"],
    route_class=DomainErrorRoute,
)

@router.get(
    "/flow/list",
    response_model=LedgerEntryListResponse,
    response_model_exclude_none=True,
)
def list_ledger_entries(
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
        LedgerEntryListRequest,
        page_index=page_index,
        page_size=page_size,
        query=query,
        filter=filter,
        sorter=sorter,
    )
    return LedgerEntryListResponse(
        status=200,
        message="ok",
        body=LedgerEntryService(db).page(request=request),
    )


@router.get("/flow/summary", response_model=LedgerEntrySummaryResponse)
def ledger_entry_summary(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be before date_to")
    return LedgerEntrySummaryResponse(
        message="Ledger entry summary returned",
        body=LedgerEntryService(db).summary(LedgerEntrySummaryQuery(
            date_from=date_from,
            date_to=date_to,
        )),
    )


@router.get("/flow/{ledger_id}", response_model=LedgerEntryDetailResponse)
def ledger_entry_detail(ledger_id: int, db: Session = Depends(get_db)):
    result = LedgerEntryService(db).detail(ledger_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Ledger entry not found")
    return LedgerEntryDetailResponse(
        status=200,
        message="ok",
        body=result,
    )
