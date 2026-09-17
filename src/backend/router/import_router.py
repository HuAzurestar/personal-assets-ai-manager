"""Versioned PIRC-9 import HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.intake import (
    ImportResponse,
    IntakeConfirmRequest,
    IntakePreviewRequest,
    IntakeReviseRequest,
)
from backend.service.target_intake_service import TargetIntakeService


router = APIRouter(
    prefix="/paam/import/v1",
    tags=["import"],
    route_class=DomainErrorRoute,
)


@router.post("/preview", response_model=ImportResponse)
def preview(payload: IntakePreviewRequest, db: Session = Depends(get_db)):
    return ImportResponse(
        status=200,
        message="ok",
        body=TargetIntakeService(db).preview(payload),
    )


@router.put("/preview/{token}", response_model=ImportResponse)
def revise(
    token: str,
    payload: IntakeReviseRequest,
    db: Session = Depends(get_db),
):
    return ImportResponse(
        status=200,
        message="ok",
        body=TargetIntakeService(db).revise(token, payload),
    )


@router.post("/preview/{token}/confirm", response_model=ImportResponse)
def confirm(
    token: str,
    payload: IntakeConfirmRequest,
    db: Session = Depends(get_db),
):
    return ImportResponse(
        status=200,
        message="ok",
        body=TargetIntakeService(db).confirm(token, payload),
    )
