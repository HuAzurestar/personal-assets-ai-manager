"""System and frontend entry-point HTTP routes."""

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from backend.core import target_database
from backend.core.config import (
    APP_DISPLAY_NAME,
    AUTOTAG_REAL_ANALYSIS,
    AUTOTAG_SYNTHETIC_ACCEPTANCE,
    RESOURCE_DIR,
)
from backend.core.job_scheduler import job_scheduler
from backend.router.dependency import validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.list_query import parse_list_request
from backend.schema.schedule import (
    ScheduleEventListRequest, ScheduleEventListResponse, ScheduleStatusResponse,
    ScheduleTaskListRequest, ScheduleTaskListResponse,
)
from backend.service.schedule_status_service import ScheduleStatusService

router = APIRouter(
    tags=["system"],
    route_class=DomainErrorRoute,
)
templates = Jinja2Templates(directory=RESOURCE_DIR / "frontend")


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="target.html",
        context={"app_name": APP_DISPLAY_NAME},
    )


@router.get("/api/health")
def health():
    return {"status": "ok", "schema": "pirc-9-target"}


@router.get(
    "/paam/system/v1/schedule/status",
    response_model=ScheduleStatusResponse,
)
def schedule_status(request: Request):
    return ScheduleStatusResponse(
        status=200,
        message="ok",
        body=_schedule_service(request).get(),
    )


@router.get("/paam/system/v1/schedule/task/list", response_model=ScheduleTaskListResponse)
def schedule_tasks(
    http_request: Request, page_index: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100), query: str | None = None,
    filter: str | None = None, sorter: str | None = None,
):
    validate_query_parameter_names(http_request, {"page_index", "page_size", "query", "filter", "sorter"})
    request = parse_list_request(
        ScheduleTaskListRequest, page_index=page_index, page_size=page_size,
        query=query, filter=filter, sorter=sorter,
    )
    return ScheduleTaskListResponse(status=200, message="ok", body=_schedule_service().tasks(request))


@router.get("/paam/system/v1/schedule/event/list", response_model=ScheduleEventListResponse)
def schedule_events(
    http_request: Request, page_index: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100), query: str | None = None,
    filter: str | None = None, sorter: str | None = None,
):
    validate_query_parameter_names(http_request, {"page_index", "page_size", "query", "filter", "sorter"})
    request = parse_list_request(
        ScheduleEventListRequest, page_index=page_index, page_size=page_size,
        query=query, filter=filter, sorter=sorter,
    )
    return ScheduleEventListResponse(status=200, message="ok", body=_schedule_service().events(request))


def _schedule_service(request: Request | None = None):
    return ScheduleStatusService(
        job_scheduler, target_database.SessionLocal,
        synthetic_acceptance_enabled=AUTOTAG_SYNTHETIC_ACCEPTANCE,
        real_analysis_enabled=AUTOTAG_REAL_ANALYSIS,
        runtime_config=getattr(request.app.state, "runtime_config", None) if request is not None else None,
    )
