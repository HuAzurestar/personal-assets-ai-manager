from fastapi import APIRouter, Request
from backend.router.error import DomainErrorRoute
from backend.error import SettingError
from backend.schema.runtime_config import RuntimeConfigResponse

router = APIRouter(prefix="/paam/system/v1/config", tags=["system-config"], route_class=DomainErrorRoute)


def _service(request):
    service = getattr(request.app.state, "runtime_config", None)
    if service is None:
        raise SettingError(503, "配置应用服务尚未就绪", code="CONFIG_NOT_READY")
    return service


@router.get("", response_model=RuntimeConfigResponse)
def get_config(request: Request):
    return RuntimeConfigResponse(status=200, message="ok", body=_service(request).describe())


@router.post("/resynchronize", response_model=RuntimeConfigResponse)
def resynchronize_config(request: Request):
    service = _service(request)
    service.reconcile()
    return RuntimeConfigResponse(status=200, message="ok", body=service.describe())
