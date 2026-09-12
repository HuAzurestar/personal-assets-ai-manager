from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.errors import MultipleTagsForView, UnknownTagSelector
from app.schemas.ledger import LedgerPageQuery
from app.services.dashboard_service import DashboardService
from app.services.drilldown_service import DrilldownService


router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard")
def dashboard(
    date_from: date | None = None,
    date_to: date | None = None,
    amount_min: float | None = Query(default=None, ge=0),
    amount_max: float | None = Query(default=None, ge=0),
    source: list[str] = Query(default=[]),
    account: list[str] = Query(default=[]),
    direction: str | None = Query(default=None, pattern="^(income|expense|transfer)$"),
    q: str | None = Query(default=None, max_length=200),
    tag: list[str] = Query(default=[]),
    db: Session = Depends(get_db),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be before date_to")
    if amount_min is not None and amount_max is not None and amount_min > amount_max:
        raise HTTPException(status_code=422, detail="amount_min must not exceed amount_max")
    query = LedgerPageQuery(
        date_from=date_from,
        date_to=date_to,
        amount_min=amount_min,
        amount_max=amount_max,
        source=tuple(source),
        account=tuple(account),
        direction=direction,
        q=q,
        tag=tuple(tag),
    )
    try:
        return DashboardService(db).summary(query)
    except MultipleTagsForView as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except UnknownTagSelector as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/ledger/drilldown")
def ledger_drilldown(
    date_from: date | None = None,
    date_to: date | None = None,
    amount_min: float | None = Query(default=None, ge=0),
    amount_max: float | None = Query(default=None, ge=0),
    source: list[str] = Query(default=[]),
    account: list[str] = Query(default=[]),
    direction: str | None = Query(default=None, pattern="^(income|expense|transfer)$"),
    q: str | None = Query(default=None, max_length=200),
    tag: list[str] = Query(default=[]),
    db: Session = Depends(get_db),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be before date_to")
    if amount_min is not None and amount_max is not None and amount_min > amount_max:
        raise HTTPException(status_code=422, detail="amount_min must not exceed amount_max")
    query = LedgerPageQuery(
        date_from=date_from,
        date_to=date_to,
        amount_min=amount_min,
        amount_max=amount_max,
        source=tuple(source),
        account=tuple(account),
        direction=direction,
        q=q,
        tag=tuple(tag),
    )
    try:
        return DrilldownService(db).report(query)
    except MultipleTagsForView as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except UnknownTagSelector as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
