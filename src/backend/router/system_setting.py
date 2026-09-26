from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core import ProtectedSecretStore
from backend.router.dependency import get_db, get_protected_secret_store
from backend.router.error import DomainErrorRoute
from backend.schema.disclosure_preview import (
    DisclosurePreviewRequest,
    DisclosurePreviewResponse,
)
from backend.schema.setting import (
    AutomationSettingResponse,
    AutomationSettingUpdateRequest,
    ModelConnectionTestResponse,
    ModelSecretStateResponse,
    ModelSecretUpdateRequest,
)
from backend.service.disclosure_preview_service import DisclosurePreviewService
from backend.service.setting_service import SettingService


router = APIRouter(
    prefix="/paam/system/v1/setting/automation",
    tags=["system-setting"],
    route_class=DomainErrorRoute,
)


def _service(
    db: Session,
    secret_store: ProtectedSecretStore,
) -> SettingService:
    return SettingService(db, secret_store)


@router.get("", response_model=AutomationSettingResponse)
def get_automation_setting(
    db: Session = Depends(get_db),
    secret_store: ProtectedSecretStore = Depends(get_protected_secret_store),
):
    return AutomationSettingResponse(
        status=200,
        message="ok",
        body=_service(db, secret_store).get_automation(),
    )


@router.put("", response_model=AutomationSettingResponse)
def update_automation_setting(
    payload: AutomationSettingUpdateRequest,
    db: Session = Depends(get_db),
    secret_store: ProtectedSecretStore = Depends(get_protected_secret_store),
):
    return AutomationSettingResponse(
        status=200,
        message="ok",
        body=_service(db, secret_store).update_automation(payload),
    )


@router.post("/disclosure_preview", response_model=DisclosurePreviewResponse)
def preview_disclosure(
    payload: DisclosurePreviewRequest,
    db: Session = Depends(get_db),
):
    return DisclosurePreviewResponse(
        status=200,
        message="ok",
        body=DisclosurePreviewService(db).preview(payload),
    )


@router.put(
    "/model/{model_id}/secret",
    response_model=ModelSecretStateResponse,
)
def update_model_secret(
    model_id: int,
    payload: ModelSecretUpdateRequest,
    db: Session = Depends(get_db),
    secret_store: ProtectedSecretStore = Depends(get_protected_secret_store),
):
    return ModelSecretStateResponse(
        status=200,
        message="ok",
        body=_service(db, secret_store).update_model_secret(
            model_id,
            payload.secret,
        ),
    )


@router.delete(
    "/model/{model_id}/secret",
    response_model=ModelSecretStateResponse,
)
def delete_model_secret(
    model_id: int,
    db: Session = Depends(get_db),
    secret_store: ProtectedSecretStore = Depends(get_protected_secret_store),
):
    return ModelSecretStateResponse(
        status=200,
        message="ok",
        body=_service(db, secret_store).delete_model_secret(model_id),
    )


@router.post(
    "/model/{model_id}/test",
    response_model=ModelConnectionTestResponse,
)
def test_model_connection(
    model_id: int,
    db: Session = Depends(get_db),
    secret_store: ProtectedSecretStore = Depends(get_protected_secret_store),
):
    return ModelConnectionTestResponse(
        status=200,
        message="ok",
        body=_service(db, secret_store).test_model_connection(model_id),
    )
