from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.core.error import MultipleTagsForView, UnknownTagSelector
from backend.schema.target_ledger import (
    TargetLedgerDetailRead,
    TargetLedgerPageQuery,
    TargetLedgerPageRead,
    TargetLedgerSummaryQuery,
    TargetLedgerSummaryRead,
)
from backend.service.target_ledger_service import TargetLedgerService
from backend.service.target_ledger_summary_service import TargetLedgerSummaryService


router = APIRouter(prefix="/paam/ledger/v1", tags=["target-ledger"])


@router.get("/entry/list", response_model=TargetLedgerPageRead)
def list_target_ledger_entries(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    date_from: date | None = None,
    date_to: date | None = None,
    ledger_type: list[str] = Query(default=[]),
    allocation_status: list[str] = Query(default=[]),
    currency_code: list[str] = Query(default=[]),
    q: str = Query(default="", max_length=200),
    account_code: str = Query(default="", max_length=120),
    tag: list[str] = Query(default=[]),
    db: Session = Depends(get_db),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be before date_to")
    service = TargetLedgerService(db)
    try:
        selectors = service.parse_tag_selectors(tag)
        return service.page(TargetLedgerPageQuery(
            page=page,
            page_size=page_size,
            sort_order=sort_order,
            date_from=date_from,
            date_to=date_to,
            ledger_type=tuple(ledger_type),
            allocation_status=tuple(allocation_status),
            currency_code=tuple(code.upper() for code in currency_code),
            q=q.strip(),
            account_code=account_code.strip(),
            tag=selectors,
        ))
    except MultipleTagsForView as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except UnknownTagSelector as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/entry/detail/{ledger_id}", response_model=TargetLedgerDetailRead)
def get_target_ledger_detail(
    ledger_id: int,
    db: Session = Depends(get_db),
):
    detail = TargetLedgerService(db).detail(ledger_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Ledger entry not found")
    return detail


@router.get("/summary", response_model=TargetLedgerSummaryRead)
def get_target_ledger_summary(
    date_from: date | None = None,
    date_to: date | None = None,
    account_code: str = Query(default="", max_length=120),
    db: Session = Depends(get_db),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be before date_to")
    return TargetLedgerSummaryService(db).summary(TargetLedgerSummaryQuery(
        date_from=date_from,
        date_to=date_to,
        account_code=account_code.strip(),
    ))
