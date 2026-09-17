from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from backend.router.dependency import get_db, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.list_query import parse_list_request
from backend.schema.target_tag import (
    TargetTagCreateRequest,
    TargetTagStatusRequest,
    TargetTagSystemNamePreviewRequest,
    TargetTagSystemNameResponse,
    TargetTagViewCreateRequest,
    TargetTagViewListRequest,
    TargetTagViewListResponse,
    TargetTagViewResponse,
)
from backend.service.target_tag_service import TargetTagService


router = APIRouter(
    prefix="/paam/tag/v1",
    tags=["tag"],
    route_class=DomainErrorRoute,
)


@router.post("/system_name/preview", response_model=TargetTagSystemNameResponse)
def preview_system_name(payload: TargetTagSystemNamePreviewRequest):
    return TargetTagSystemNameResponse(
        status=200,
        message="ok",
        body=TargetTagService.preview_system_name(payload.name),
    )


@router.get(
    "/view/list",
    response_model=TargetTagViewListResponse,
    response_model_exclude_none=True,
)
def list_views(
    http_request: Request,
    page_index: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    query: str | None = Query(default=None),
    filter: str | None = Query(default=None),
    sorter: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    validate_query_parameter_names(
        http_request,
        {"page_index", "page_size", "query", "filter", "sorter"},
    )
    request = parse_list_request(
        TargetTagViewListRequest,
        page_index=page_index,
        page_size=page_size,
        query=query,
        filter=filter,
        sorter=sorter,
    )
    return TargetTagViewListResponse(
        status=200,
        message="ok",
        body=TargetTagService(db).list(request=request),
    )


@router.post("/view", response_model=TargetTagViewResponse)
def create_view(payload: TargetTagViewCreateRequest, db: Session = Depends(get_db)):
    return TargetTagViewResponse(
        status=200,
        message="ok",
        body=TargetTagService(db).create_view(payload),
    )


@router.put("/view/{view_id}", response_model=TargetTagViewResponse)
def set_view_status(
    view_id: int,
    payload: TargetTagStatusRequest,
    db: Session = Depends(get_db),
):
    return TargetTagViewResponse(
        status=200,
        message="ok",
        body=TargetTagService(db).set_view_status(view_id, payload),
    )


@router.post("/view/{view_id}/tag", response_model=TargetTagViewResponse)
def create_tag(
    view_id: int,
    payload: TargetTagCreateRequest,
    db: Session = Depends(get_db),
):
    return TargetTagViewResponse(
        status=200,
        message="ok",
        body=TargetTagService(db).create_tag(view_id, payload),
    )


@router.put("/view/{view_id}/tag/{tag_id}", response_model=TargetTagViewResponse)
def set_tag_status(
    view_id: int,
    tag_id: int,
    payload: TargetTagStatusRequest,
    db: Session = Depends(get_db),
):
    return TargetTagViewResponse(
        status=200,
        message="ok",
        body=TargetTagService(db).set_tag_status(view_id, tag_id, payload)
    )
