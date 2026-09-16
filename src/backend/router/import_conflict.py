"""Import Fact conflict review HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.intake import (
    ImportFactConflictPageResponse,
    ImportFactConflictResponse,
)
from backend.schema.list_query import parse_query_object
from backend.schema.target_review import (
    TargetFactConflictFilter,
    TargetFactConflictResolveRequest,
    TargetFactConflictSorter,
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
    response_model=ImportFactConflictPageResponse,
)
def list_conflicts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
    filter: str = Query(default="{}"),
    sorter: str = Query(default='{"field":"updated_time","order":"desc"}'),
    db: Session = Depends(get_db),
):
    filter_value = parse_query_object(filter, TargetFactConflictFilter, "filter")
    sorter_value = parse_query_object(sorter, TargetFactConflictSorter, "sorter")
    return ImportFactConflictPageResponse(
        message="Fact conflicts listed",
        body=TargetFactConflictService(db).page(
            page,
            page_size,
            q.strip(),
            filter_value,
            sorter_value,
        )
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
