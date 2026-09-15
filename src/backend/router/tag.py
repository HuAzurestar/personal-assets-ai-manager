from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.target_tag import (
    TargetTagCreateRequest,
    TargetTagStatusRequest,
    TargetTagViewCreateRequest,
    TargetTagViewListResponse,
    TargetTagViewResponse,
)
from backend.service.target_tag_service import TargetTagService


router = APIRouter(
    prefix="/paam/tag/v1",
    tags=["tag"],
    route_class=DomainErrorRoute,
)


@router.get("/view/list", response_model=TargetTagViewListResponse)
def list_views(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    include_archived: bool = False,
    db: Session = Depends(get_db),
):
    return TargetTagViewListResponse(
        message="Tag view list retrieved",
        body=TargetTagService(db).list(page, page_size, include_archived),
    )


@router.post("/view", response_model=TargetTagViewResponse)
def create_view(payload: TargetTagViewCreateRequest, db: Session = Depends(get_db)):
    return TargetTagViewResponse(
        message="Tag view created",
        body=TargetTagService(db).create_view(payload),
    )


@router.put("/view/{view_id}", response_model=TargetTagViewResponse)
def set_view_status(
    view_id: int,
    payload: TargetTagStatusRequest,
    db: Session = Depends(get_db),
):
    return TargetTagViewResponse(
        message="Tag view updated",
        body=TargetTagService(db).set_view_status(view_id, payload),
    )


@router.post("/view/{view_id}/tag", response_model=TargetTagViewResponse)
def create_tag(
    view_id: int,
    payload: TargetTagCreateRequest,
    db: Session = Depends(get_db),
):
    return TargetTagViewResponse(
        message="Tag created",
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
        message="Tag updated",
        body=TargetTagService(db).set_tag_status(view_id, tag_id, payload)
    )
