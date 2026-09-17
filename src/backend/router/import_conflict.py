"""Import Fact conflict review HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from backend.router.dependency import get_db, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.intake import (
    ImportFactConflictListResponse,
    ImportFactConflictResponse,
)
from backend.schema.list_query import parse_list_request
from backend.schema.target_review import (
    TargetFactConflictListRequest,
    TargetFactConflictResolveRequest,
    TargetReviewTransitionRequest,
)
from backend.service.target_fact_conflict_service import TargetFactConflictService


router = APIRouter(
    prefix="/paam/import/v1",
    tags=["import-fact-conflict"],
    route_class=DomainErrorRoute,
)


@router.get(
    "/fact_conflict/list",
    response_model=ImportFactConflictListResponse,
    response_model_exclude_none=True,
)
def list_conflicts(
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
        TargetFactConflictListRequest,
        page_index=page_index,
        page_size=page_size,
        query=query,
        filter=filter,
        sorter=sorter,
    )
    return ImportFactConflictListResponse(
        status=200,
        message="ok",
        body=TargetFactConflictService(db).page(request=request),
    )


@router.get(
    "/fact_conflict/{conflict_id}",
    response_model=ImportFactConflictResponse,
)
def conflict_detail(conflict_id: int, db: Session = Depends(get_db)):
    return ImportFactConflictResponse(
        body=TargetFactConflictService(db).detail(conflict_id)
    )


@router.post(
    "/fact_conflict/{conflict_id}/resolve",
    response_model=ImportFactConflictResponse,
)
def resolve_conflict(
    conflict_id: int,
    payload: TargetFactConflictResolveRequest,
    db: Session = Depends(get_db),
):
    return ImportFactConflictResponse(
        message="Fact conflict resolved",
        body=TargetFactConflictService(db).resolve(conflict_id, payload)
    )


@router.post(
    "/fact_conflict/{conflict_id}/dismiss",
    response_model=ImportFactConflictResponse,
)
def dismiss_conflict(
    conflict_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return ImportFactConflictResponse(
        message="Fact conflict dismissed",
        body=TargetFactConflictService(db).dismiss(conflict_id, payload)
    )


@router.post(
    "/fact_conflict/{conflict_id}/reopen",
    response_model=ImportFactConflictResponse,
)
def reopen_conflict(
    conflict_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return ImportFactConflictResponse(
        message="Fact conflict reopened",
        body=TargetFactConflictService(db).reopen(conflict_id, payload)
    )
