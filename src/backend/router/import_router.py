"""Versioned PIRC-9 import HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.intake import (
    IntakeConfirmRequest,
    IntakePreviewRequest,
    IntakeResponse,
    IntakeReviseRequest,
)
from backend.service.target_intake_service import TargetIntakeService


router = APIRouter(tags=["target-imports"], route_class=DomainErrorRoute)


def _envelope(body: dict[str, object] | list[dict[str, object]]) -> IntakeResponse:
    return IntakeResponse(body=body)


@router.post("/paam/import/v1/preview", response_model=IntakeResponse)
def preview(payload: IntakePreviewRequest, db: Session = Depends(get_db)):
    return _envelope(TargetIntakeService(db).preview(payload))


@router.put("/paam/import/v1/preview/{token}", response_model=IntakeResponse)
def revise(
    token: str,
    payload: IntakeReviseRequest,
    db: Session = Depends(get_db),
):
    return _envelope(TargetIntakeService(db).revise(token, payload))


@router.post("/paam/import/v1/preview/confirm/{token}", response_model=IntakeResponse)
def confirm(
    token: str,
    payload: IntakeConfirmRequest,
    db: Session = Depends(get_db),
):
    return _envelope(TargetIntakeService(db).confirm(token, payload))


@router.get("/paam/import/v1/batch/list", response_model=IntakeResponse)
def history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    q: str = Query(default="", max_length=120),
    account: str = Query(default="", max_length=120),
    db: Session = Depends(get_db),
):
    return _envelope(TargetIntakeService(db).history(
        page=page,
        page_size=page_size,
        q=q.strip(),
        account=account.strip(),
    ))


@router.get("/paam/import/v1/batch/row/list", response_model=IntakeResponse)
def rows(
    batch_id: int = Query(ge=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return _envelope(TargetIntakeService(db).rows(
        batch_id,
        page,
        page_size,
    ))


@router.get("/paam/import/v1/account/list", response_model=IntakeResponse)
def accounts(db: Session = Depends(get_db)):
    return _envelope(TargetIntakeService(db).accounts())
