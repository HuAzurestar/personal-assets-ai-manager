"""Import File PO inspection HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from backend.router.dependency import get_db, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.import_file import (
    ImportFileDetailResponse,
    ImportFileListRequest,
    ImportFileListResponse,
    ImportFileSummaryResponse,
    ImportFileTransactionFactListResponse,
)
from backend.schema.list_query import parse_list_request
from backend.service.import_file_service import ImportFileService


router = APIRouter(
    prefix="/paam/import/v1",
    tags=["import-file"],
    route_class=DomainErrorRoute,
)


@router.get(
    "/import_file/list",
    response_model=ImportFileListResponse,
    response_model_exclude_none=True,
)
def import_file_list(
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
        ImportFileListRequest,
        page_index=page_index,
        page_size=page_size,
        query=query,
        filter=filter,
        sorter=sorter,
    )
    return ImportFileListResponse(
        status=200,
        message="ok",
        body=ImportFileService(db).page(request=request),
    )


@router.get("/import_file/summary", response_model=ImportFileSummaryResponse)
def import_file_summary(
    http_request: Request,
    filter: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    validate_query_parameter_names(http_request, {"filter"})
    request = parse_list_request(ImportFileListRequest, filter=filter)
    return ImportFileSummaryResponse(
        status=200,
        message="ok",
        body=ImportFileService(db).summary(request=request),
    )


@router.get("/import_file/{import_file_id}", response_model=ImportFileDetailResponse)
def import_file_detail(
    import_file_id: int,
    db: Session = Depends(get_db),
):
    return ImportFileDetailResponse(
        status=200,
        message="ok",
        body=ImportFileService(db).detail(import_file_id),
    )


@router.get(
    "/import_file/{import_file_id}/transaction_fact/list",
    response_model=ImportFileTransactionFactListResponse,
)
def import_file_transaction_fact_list(
    import_file_id: int,
    http_request: Request,
    db: Session = Depends(get_db),
):
    validate_query_parameter_names(http_request, set())
    return ImportFileTransactionFactListResponse(
        status=200,
        message="ok",
        body=ImportFileService(db).transaction_facts(import_file_id),
    )
