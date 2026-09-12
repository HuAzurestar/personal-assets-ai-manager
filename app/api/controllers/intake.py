from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.intake import (
    IntakeConfirmRequest,
    IntakePreviewRequest,
    IntakeReviseRequest,
)
from app.services.intake_service import IntakeError, IntakeService


router = APIRouter(tags=["imports"])


def _run(operation: Callable[[], object]):
    try:
        return operation()
    except IntakeError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error


# Compatibility endpoints used by the current local UI. New clients use /paam/import/v1.
@router.get("/api/intake/history")
def legacy_history(db: Session = Depends(get_db)):
    return _run(lambda: IntakeService(db).history())


@router.get("/api/accounts")
def legacy_accounts(db: Session = Depends(get_db)):
    return _run(lambda: IntakeService(db).accounts())


@router.post("/api/intake/preview")
def legacy_preview(payload: IntakePreviewRequest, db: Session = Depends(get_db)):
    return _run(lambda: IntakeService(db).preview(payload))


@router.put("/api/intake/{token}/preview")
def legacy_revise(
    token: str,
    payload: IntakeReviseRequest,
    db: Session = Depends(get_db),
):
    return _run(lambda: IntakeService(db).revise(token, payload))


@router.post("/api/intake/{token}/confirm")
def legacy_confirm(
    token: str,
    payload: IntakeConfirmRequest,
    db: Session = Depends(get_db),
):
    return _run(lambda: IntakeService(db).confirm(token, payload))


@router.get("/api/intake/batches/{batch_id}/rows")
def legacy_rows(batch_id: int, db: Session = Depends(get_db)):
    return _run(lambda: IntakeService(db).rows(batch_id))
