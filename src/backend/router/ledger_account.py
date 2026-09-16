"""Ledger-owned Account subresource HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.ledger_account import LedgerAccountResponse, LedgerAccountUpdateRequest
from backend.service.ledger_account_service import LedgerAccountService


router = APIRouter(
    prefix="/paam/ledger/v1",
    tags=["ledger-account"],
    route_class=DomainErrorRoute,
)


@router.get("/flow/{ledger_id}/account", response_model=LedgerAccountResponse)
def get_account(ledger_id: int, db: Session = Depends(get_db)):
    return LedgerAccountResponse(
        message="Ledger account returned",
        body=LedgerAccountService(db).get(ledger_id),
    )


@router.put("/flow/{ledger_id}/account", response_model=LedgerAccountResponse)
def update_account(
    ledger_id: int,
    payload: LedgerAccountUpdateRequest,
    db: Session = Depends(get_db),
):
    return LedgerAccountResponse(
        message="Ledger account updated",
        body=LedgerAccountService(db).update(ledger_id, payload),
    )
