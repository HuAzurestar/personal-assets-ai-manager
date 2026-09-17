from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.list_query import parse_query_object
from backend.schema.ledger_entry import (
    LedgerEntryDetailResponse,
    LedgerEntryFilter,
    LedgerEntryPageQuery,
    LedgerEntryPageResponse,
    LedgerEntrySorter,
    LedgerEntrySummaryQuery,
    LedgerEntrySummaryResponse,
)
from backend.service.ledger_entry_service import LedgerEntryService


router = APIRouter(
    prefix="/paam/ledger/v1",
    tags=["ledger-flow"],
    route_class=DomainErrorRoute,
)

@router.get("/flow/list", response_model=LedgerEntryPageResponse)
def list_ledger_entries(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    date_from: date | None = None,
    date_to: date | None = None,
    entry_type: list[int] = Query(default=[]),
    currency_code: list[str] = Query(default=[]),
    q: str = Query(default="", max_length=200),
    filter: str = Query(default="{}"),
    sorter: str = Query(default='{"field":"occurred_time","order":"desc"}'),
    db: Session = Depends(get_db),
):
    filter_value = parse_query_object(filter, LedgerEntryFilter, "filter")
    sorter_value = parse_query_object(sorter, LedgerEntrySorter, "sorter")
    effective_date_from = filter_value.date_from or date_from
    effective_date_to = filter_value.date_to or date_to
    effective_types = filter_value.entry_type or entry_type
    effective_currencies = filter_value.currency_code or currency_code
    if effective_date_from and effective_date_to and effective_date_from > effective_date_to:
        raise HTTPException(status_code=422, detail="date_from must be before date_to")
    try:
        invalid = sorted(set(effective_types) - {0, 1, 2})
        if invalid:
            raise ValueError(f"unknown ledger entry types: {invalid}")
        return LedgerEntryPageResponse(
            message="Ledger entries listed",
            body=LedgerEntryService(db).page(LedgerEntryPageQuery(
                page=page,
                page_size=page_size,
                date_from=effective_date_from,
                date_to=effective_date_to,
                entry_type=tuple(effective_types),
                currency_code=tuple(code.upper() for code in effective_currencies),
                entry_direction=filter_value.entry_direction,
                account_code=filter_value.account_code or "",
                q=q.strip(),
                sort_field=sorter_value.field,
                sort_order=sorter_value.order,
            )),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


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
        message="Ledger entry returned",
        body=result,
    )
