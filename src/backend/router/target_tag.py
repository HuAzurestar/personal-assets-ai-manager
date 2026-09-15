from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.target_tag import (
    TargetTagAssignmentRequest,
    TargetTagAssignmentResponse,
    TargetTagCreateRequest,
    TargetTagStatusRequest,
    TargetTagViewCreateRequest,
    TargetTagViewListResponse,
    TargetTagViewResponse,
)
from backend.service.target_tag_assignment_service import TargetTagAssignmentService
from backend.service.target_tag_service import TargetTagService


router = APIRouter(
    prefix="/paam/tag/v1",
    tags=["target-tags"],
    route_class=DomainErrorRoute,
)


@router.get("/view/list", response_model=TargetTagViewListResponse)
def list_views(include_archived: bool = False, db: Session = Depends(get_db)):
    return TargetTagViewListResponse(body=TargetTagService(db).list(include_archived))


@router.post("/view/create", response_model=TargetTagViewResponse)
def create_view(payload: TargetTagViewCreateRequest, db: Session = Depends(get_db)):
    return TargetTagViewResponse(body=TargetTagService(db).create_view(payload))


@router.put("/view/status/{view_id}", response_model=TargetTagViewResponse)
def set_view_status(
    view_id: int,
    payload: TargetTagStatusRequest,
    db: Session = Depends(get_db),
):
    return TargetTagViewResponse(body=TargetTagService(db).set_view_status(view_id, payload))


@router.post("/tag/create/{view_id}", response_model=TargetTagViewResponse)
def create_tag(
    view_id: int,
    payload: TargetTagCreateRequest,
    db: Session = Depends(get_db),
):
    return TargetTagViewResponse(body=TargetTagService(db).create_tag(view_id, payload))


@router.put("/tag/status/{view_id}/{tag_id}", response_model=TargetTagViewResponse)
def set_tag_status(
    view_id: int,
    tag_id: int,
    payload: TargetTagStatusRequest,
    db: Session = Depends(get_db),
):
    return TargetTagViewResponse(
        body=TargetTagService(db).set_tag_status(view_id, tag_id, payload)
    )


@router.put("/assignment/set/{ledger_id}", response_model=TargetTagAssignmentResponse)
def set_assignment(
    ledger_id: int,
    payload: TargetTagAssignmentRequest,
    db: Session = Depends(get_db),
):
    return TargetTagAssignmentResponse(
        body=TargetTagAssignmentService(db).assign(ledger_id, payload)
    )
