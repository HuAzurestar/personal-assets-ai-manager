"""Versioned PIRC-9 import HTTP adapter."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.intake import (
    IntakeConfirmRequest,
    IntakePreviewRequest,
    IntakeResponse,
    IntakeReviseRequest,
)
from app.services.target_intake_service import TargetIntakeError, TargetIntakeService


router = APIRouter(tags=["target-imports"])


def _run(operation: Callable[[], object]):
    try:
        return operation()
    except TargetIntakeError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error


def _envelope(body: dict[str, object] | list[dict[str, object]]) -> IntakeResponse:
    return IntakeResponse(body=body)


@router.post("/paam/import/v1/preview", response_model=IntakeResponse)
def preview(payload: IntakePreviewRequest, db: Session = Depends(get_db)):
    return _envelope(_run(lambda: TargetIntakeService(db).preview(payload)))


@router.put("/paam/import/v1/preview/{token}", response_model=IntakeResponse)
def revise(
    token: str,
    payload: IntakeReviseRequest,
    db: Session = Depends(get_db),
):
    return _envelope(_run(lambda: TargetIntakeService(db).revise(token, payload)))


@router.post("/paam/import/v1/preview/confirm/{token}", response_model=IntakeResponse)
def confirm(
    token: str,
    payload: IntakeConfirmRequest,
    db: Session = Depends(get_db),
):
    return _envelope(_run(lambda: TargetIntakeService(db).confirm(token, payload)))


@router.get("/paam/import/v1/batch/list", response_model=IntakeResponse)
def history(db: Session = Depends(get_db)):
    return _envelope(_run(lambda: TargetIntakeService(db).history()))


@router.get("/paam/import/v1/batch/row/list", response_model=IntakeResponse)
def rows(batch_id: int = Query(ge=1), db: Session = Depends(get_db)):
    return _envelope(_run(lambda: TargetIntakeService(db).rows(batch_id)))


@router.get("/paam/import/v1/account/list", response_model=IntakeResponse)
def accounts(db: Session = Depends(get_db)):
    return _envelope(_run(lambda: TargetIntakeService(db).accounts()))
