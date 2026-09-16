"""Import File PO inspection HTTP adapter."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.import_file import (
    ImportFileDetailResponse,
    ImportFileFilter,
    ImportFilePageResponse,
    ImportFileSorter,
    ImportFileTransactionFactPageResponse,
)
from backend.schema.list_query import parse_query_object
from backend.schema.transaction_fact import TransactionFactFilter, TransactionFactSorter
from backend.service.import_file_service import ImportFileService


router = APIRouter(
    prefix="/paam/import/v1",
    tags=["import-file"],
    route_class=DomainErrorRoute,
)


@router.get("/import_file/list", response_model=ImportFilePageResponse)
def import_file_list(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
    filter: str = Query(default="{}"),
    sorter: str = Query(default='{"field":"created_time","order":"desc"}'),
    db: Session = Depends(get_db),
):
    filter_value = parse_query_object(filter, ImportFileFilter, "filter")
    sorter_value = parse_query_object(sorter, ImportFileSorter, "sorter")
    return ImportFilePageResponse(
        message="Import files listed",
        body=ImportFileService(db).page(
            page=page,
            page_size=page_size,
            q=q.strip(),
            filter_value=filter_value,
            sorter=sorter_value,
        ),
    )


@router.get("/import_file/{import_file_id}", response_model=ImportFileDetailResponse)
def import_file_detail(
    import_file_id: int,
    db: Session = Depends(get_db),
):
    return ImportFileDetailResponse(
        message="Import file loaded",
        body=ImportFileService(db).detail(import_file_id),
    )


@router.get(
    "/import_file/{import_file_id}/transaction_fact/list",
    response_model=ImportFileTransactionFactPageResponse,
)
def import_file_transaction_fact_list(
    import_file_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
    filter: str = Query(default="{}"),
    sorter: str = Query(default='{"field":"occurred_time","order":"desc"}'),
    db: Session = Depends(get_db),
):
    filter_value = parse_query_object(filter, TransactionFactFilter, "filter")
    sorter_value = parse_query_object(sorter, TransactionFactSorter, "sorter")
    return ImportFileTransactionFactPageResponse(
        message="Import file transaction facts listed",
        body=ImportFileService(db).transaction_fact_page(
            import_file_id,
            page=page,
            page_size=page_size,
            q=q.strip(),
            filter_value=filter_value,
            sorter=sorter_value,
        ),
    )
