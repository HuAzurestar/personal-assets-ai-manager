from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.errors import RefundCommandError
from app.schemas import NatureRequest, RefundAllocationCreate, RefundAllocationRead, UndoRequest
from app.schemas.refund import (
    RefundAllocationAuditRead,
    RefundListItemRead,
    RefundNatureCommandRead,
    RefundNatureRead,
    RefundPageRead,
)
from app.services.refund_service import RefundService


router = APIRouter(tags=["review"])


@router.get("/api/refunds", response_model=list[RefundListItemRead])
def list_refunds(db: Session = Depends(get_db)):
    return RefundService(db).list()


@router.get("/api/refunds/page", response_model=RefundPageRead)
def page_refunds(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return RefundService(db).page(page=page, page_size=page_size)


@router.get("/api/refunds/{bill_id}", response_model=RefundListItemRead)
def get_refund(bill_id: int, db: Session = Depends(get_db)):
    try:
        return RefundService(db).get(bill_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post(
    "/api/refund-allocations",
    response_model=RefundAllocationRead,
    status_code=201,
)
def create_refund_allocation(
    payload: RefundAllocationCreate,
    db: Session = Depends(get_db),
):
    try:
        return RefundService(db).create_allocation(payload)
    except RefundCommandError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/api/refund-allocations/{allocation_id}/undo",
    response_model=RefundAllocationRead,
)
def undo_refund_allocation(
    allocation_id: int,
    payload: UndoRequest,
    db: Session = Depends(get_db),
):
    try:
        return RefundService(db).undo_allocation(allocation_id, payload)
    except RefundCommandError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    "/api/refund-allocations/{allocation_id}/audits",
    response_model=list[RefundAllocationAuditRead],
)
def list_refund_allocation_audits(
    allocation_id: int,
    db: Session = Depends(get_db),
):
    try:
        return RefundService(db).allocation_audits(allocation_id)
    except RefundCommandError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.put(
    "/api/transactions/{bill_id}/nature",
    response_model=RefundNatureCommandRead,
)
def set_bill_nature(
    bill_id: int,
    payload: NatureRequest,
    db: Session = Depends(get_db),
):
    try:
        return RefundService(db).set_nature(bill_id, payload)
    except RefundCommandError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    "/api/transactions/{bill_id}/nature",
    response_model=RefundNatureRead,
)
def get_bill_nature(bill_id: int, db: Session = Depends(get_db)):
    try:
        return RefundService(db).get_nature(bill_id)
    except RefundCommandError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
