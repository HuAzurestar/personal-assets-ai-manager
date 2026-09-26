from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from backend.router.dependency import get_db, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.list_query import parse_list_request
from backend.schema.tag_assignment_request import (
    TagAssignmentBatchRequest,
    TagAssignmentBatchResponse,
    TagAssignmentRequestListRequest,
    TagAssignmentRequestListResponse,
    TagAssignmentRequestResponse,
)
from backend.service.tag_assignment_request_service import (
    TagAssignmentRequestService,
)

router = APIRouter(
    prefix="/paam/tag/v1/assignment_request",
    tags=["tag-assignment-request"],
    route_class=DomainErrorRoute,
)


@router.get(
    "/list",
    response_model=TagAssignmentRequestListResponse,
    response_model_exclude_none=True,
)
def list_requests(
    http_request: Request,
    page_index: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    query: str | None = Query(default=None),
    filter: str | None = Query(default=None),
    sorter: str | None = Query(default=None),
    db: Session = Depends(get_db),  # noqa: B008
):
    validate_query_parameter_names(
        http_request,
        {"page_index", "page_size", "query", "filter", "sorter"},
    )
    request = parse_list_request(
        TagAssignmentRequestListRequest,
        page_index=page_index,
        page_size=page_size,
        query=query,
        filter=filter,
        sorter=sorter,
    )
    return TagAssignmentRequestListResponse(
        status=200,
        message="ok",
        body=TagAssignmentRequestService(db).list(request),
    )


@router.get("/{request_id}", response_model=TagAssignmentRequestResponse)
def get_request(
    request_id: int,
    db: Session = Depends(get_db),  # noqa: B008
):
    return TagAssignmentRequestResponse(
        status=200,
        message="ok",
        body=TagAssignmentRequestService(db).get(request_id),
    )


@router.post("/batch_approve", response_model=TagAssignmentBatchResponse)
def approve_requests(
    payload: TagAssignmentBatchRequest,
    db: Session = Depends(get_db),  # noqa: B008
):
    return TagAssignmentBatchResponse(
        status=200,
        message="ok",
        body=TagAssignmentRequestService(db).approve(payload.request_ids),
    )


@router.post("/batch_reject", response_model=TagAssignmentBatchResponse)
def reject_requests(
    payload: TagAssignmentBatchRequest,
    db: Session = Depends(get_db),  # noqa: B008
):
    return TagAssignmentBatchResponse(
        status=200,
        message="ok",
        body=TagAssignmentRequestService(db).reject(payload.request_ids),
    )
