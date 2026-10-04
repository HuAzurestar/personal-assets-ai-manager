from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from backend.core import ProtectedSecretStore
from backend.router.dependency import get_db, get_protected_secret_store, get_platform_middleware
from backend.router.error import DomainErrorRoute
from backend.schema.disclosure_preview import (
    DisclosurePreviewRequest,
    DisclosurePreviewResponse,
)
from backend.schema.setting import (
    AutomationSettingResponse,
    AutomationSettingUpdateRequest,
    ModelConnectionTestResponse,
    ModelConnectionCheckRequest,
    ModelConnectionCheckResponse,
    ModelCatalogRequest,
    ModelCatalogResponse,
    ModelSecretStateResponse,
    ModelSecretUpdateRequest,
)
from backend.service.disclosure_preview_service import DisclosurePreviewService
from backend.service.setting_service import SettingService
from backend.service.model_connection_service import ModelConnectionService
from backend.service.model_catalog_service import ModelCatalogService
from backend.service.configured_llm_analyzer import provider_secret_reader


router = APIRouter(
    prefix="/paam/system/v1/setting/automation",
    tags=["system-setting"],
    route_class=DomainErrorRoute,
)


def _service(
    db: Session,
    secret_store: ProtectedSecretStore,
    platform,
) -> SettingService:
    return platform.setting(db, secret_store=secret_store)


@router.get("", response_model=AutomationSettingResponse)
def get_automation_setting(
    db: Session = Depends(get_db),
    secret_store: ProtectedSecretStore = Depends(get_protected_secret_store),
    platform=Depends(get_platform_middleware),
):
    return AutomationSettingResponse(
        status=200,
        message="ok",
        body=_service(db, secret_store, platform).get_automation(),
    )


@router.put("", response_model=AutomationSettingResponse)
def update_automation_setting(
    payload: AutomationSettingUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    secret_store: ProtectedSecretStore = Depends(get_protected_secret_store),
    platform=Depends(get_platform_middleware),
):
    schedule = getattr(request.app.state, "auto_tag_schedule", None)
    service = platform.setting(
        db, secret_store=secret_store,
        on_scan_setting_changed=getattr(schedule, "sync_enabled", None),
    )
    body = service.update_automation(payload)
    return AutomationSettingResponse(
        status=200,
        message="ok",
        body=body,
        warnings=service.warnings,
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
    platform=Depends(get_platform_middleware),
):
    return ModelSecretStateResponse(
        status=200,
        message="ok",
        body=_service(db, secret_store, platform).update_model_secret(
            model_id,
            payload.secret,
        ),
    )


@router.post("/model/catalog", response_model=ModelCatalogResponse)
def list_provider_models(
    payload: ModelCatalogRequest,
    db: Session = Depends(get_db),
):
    return ModelCatalogResponse(
        status=200,
        message="ok",
        body=ModelCatalogService(db, provider_secret_reader).list(payload),
    )


@router.delete(
    "/model/{model_id}/secret",
    response_model=ModelSecretStateResponse,
)
def delete_model_secret(
    model_id: int,
    db: Session = Depends(get_db),
    secret_store: ProtectedSecretStore = Depends(get_protected_secret_store),
    platform=Depends(get_platform_middleware),
):
    return ModelSecretStateResponse(
        status=200,
        message="ok",
        body=_service(db, secret_store, platform).delete_model_secret(model_id),
    )


@router.post(
    "/model/{model_id}/test",
    response_model=ModelConnectionTestResponse,
)
def test_model_connection(
    model_id: int,
    db: Session = Depends(get_db),
    secret_store: ProtectedSecretStore = Depends(get_protected_secret_store),
    platform=Depends(get_platform_middleware),
):
    return ModelConnectionTestResponse(
        status=200,
        message="ok",
        body=_service(db, secret_store, platform).test_model_connection(model_id),
    )


def get_connection_secret_reader():
    return provider_secret_reader


@router.post("/model/{model_id}/connection_check", response_model=ModelConnectionCheckResponse)
def check_model_connection(
    model_id: int,
    payload: ModelConnectionCheckRequest,
    db: Session = Depends(get_db),
    secret_store: ProtectedSecretStore = Depends(get_protected_secret_store),
    secret_reader=Depends(get_connection_secret_reader),
):
    return ModelConnectionCheckResponse(
        status=200, message="ok",
        body=ModelConnectionService(db, secret_store, secret_reader).check(
            model_id, payload.expected_updated_time,
        ),
    )
