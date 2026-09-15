"""Versioned PIRC-9 import HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
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


router = APIRouter(
    prefix="/paam/import/v1",
    tags=["import"],
    route_class=DomainErrorRoute,
)


@router.post("/preview", response_model=IntakeResponse)
def preview(payload: IntakePreviewRequest, db: Session = Depends(get_db)):
    return IntakeResponse(body=TargetIntakeService(db).preview(payload))


@router.put("/preview/{token}", response_model=IntakeResponse)
def revise(
    token: str,
    payload: IntakeReviseRequest,
    db: Session = Depends(get_db),
):
    return IntakeResponse(body=TargetIntakeService(db).revise(token, payload))


@router.post("/preview/{token}/confirm", response_model=IntakeResponse)
def confirm(
    token: str,
    payload: IntakeConfirmRequest,
    db: Session = Depends(get_db),
):
    return IntakeResponse(body=TargetIntakeService(db).confirm(token, payload))


@router.get("/batch/list", response_model=IntakeResponse)
def history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    q: str = Query(default="", max_length=120),
    account: str = Query(default="", max_length=120),
    db: Session = Depends(get_db),
):
    return IntakeResponse(
        body=TargetIntakeService(db).history(
            page=page,
            page_size=page_size,
            q=q.strip(),
            account=account.strip(),
        )
    )


@router.get("/batch/{batch_id}/row/list", response_model=IntakeResponse)
def rows(
    batch_id: int = Path(ge=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return IntakeResponse(
        body=TargetIntakeService(db).rows(
            batch_id,
            page,
            page_size,
        )
    )


@router.get("/account/list", response_model=IntakeResponse)
def accounts(db: Session = Depends(get_db)):
    return IntakeResponse(body=TargetIntakeService(db).accounts())
