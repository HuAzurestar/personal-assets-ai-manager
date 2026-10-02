"""Persisted source inspection and loss-of-response verification."""
import json
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from backend.router.dependency import ResourceId, get_db, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.router.bounded_query import bounded_search_dependency
from backend.schema.list_query import parse_list_request
from backend.schema.import_source_read import (SourceFileListRequest, SourceFileSearchRequest, SourceRowListRequest,
    SourceRowSearchRequest, SourceFileListResponse, SourceFileSearchResponse, SourceFileDetailResponse,
    SourceRowListResponse, SourceRowSearchResponse, SourceRowDetailResponse, SourceRelationsResponse)
from backend.schema.import_file import (ImportFileListRequest, ImportFileSummaryResponse, ImportFileTransactionFactListResponse)
from backend.service.import_source_service import ImportSourceService
from backend.service.import_file_service import ImportFileService
from backend.mapper.import_batch_mapper import fail

router = APIRouter(prefix="/paam/import/v1", tags=["import-file"], route_class=DomainErrorRoute)


def source_list(request_type):
    def parse(http_request: Request, page_index: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
              query: str | None = None, filter: str | None = None, sorter: str | None = None):
        validate_query_parameter_names(http_request, {"page_index", "page_size", "query", "filter", "sorter"})
        return parse_list_request(request_type, page_index=page_index, page_size=page_size, query=query, filter=filter, sorter=sorter)
    return parse


@router.get("/import_file/list", response_model=SourceFileListResponse)
def file_list(request: SourceFileListRequest = Depends(source_list(SourceFileListRequest)), db: Session = Depends(get_db)):
    return SourceFileListResponse(status=200, message="ok", body=ImportSourceService(db).page(request))


@router.get("/import_file/search", response_model=SourceFileSearchResponse)
def file_search(request: SourceFileSearchRequest = Depends(bounded_search_dependency(SourceFileSearchRequest)), db: Session = Depends(get_db)):
    return SourceFileSearchResponse(status=200, message="ok", body=ImportSourceService(db).page(request, search=True))


@router.get("/import_file/summary", response_model=ImportFileSummaryResponse)
def file_summary(http_request: Request, filter: str | None = None, db: Session = Depends(get_db)):
    validate_query_parameter_names(http_request, {"filter"})
    return ImportFileSummaryResponse(status=200, message="ok", body=ImportFileService(db).summary(
        request=parse_list_request(ImportFileListRequest, filter=filter)))


@router.get("/import_file/{import_file_id}", response_model=SourceFileDetailResponse)
def file_detail(import_file_id: ResourceId, db: Session = Depends(get_db)):
    return SourceFileDetailResponse(status=200, message="ok", body=ImportSourceService(db).detail(import_file_id))


@router.get("/import_file/{import_file_id}/transaction_fact/list", response_model=ImportFileTransactionFactListResponse)
def transaction_facts(http_request: Request, import_file_id: ResourceId, db: Session = Depends(get_db)):
    # Preserve this existing dedicated non-paged read contract.
    validate_query_parameter_names(http_request, set())
    return ImportFileTransactionFactListResponse(status=200, message="ok", body=ImportFileService(db).transaction_facts(import_file_id))


@router.get("/import_file/{import_file_id}/row/list", response_model=SourceRowListResponse)
def row_list(import_file_id: ResourceId, request: SourceRowListRequest = Depends(source_list(SourceRowListRequest)), db: Session = Depends(get_db)):
    return SourceRowListResponse(status=200, message="ok", body=ImportSourceService(db).page(request, file_id=import_file_id))


@router.get("/import_file/{import_file_id}/row/search", response_model=SourceRowSearchResponse)
def row_search(import_file_id: ResourceId, request: SourceRowSearchRequest = Depends(bounded_search_dependency(SourceRowSearchRequest)), db: Session = Depends(get_db)):
    return SourceRowSearchResponse(status=200, message="ok", body=ImportSourceService(db).page(request, file_id=import_file_id, search=True))


@router.get("/import_file/{import_file_id}/row/relations", response_model=SourceRelationsResponse)
def row_relations(http_request: Request, import_file_id: ResourceId, row_ids: str = Query(max_length=4096), db: Session = Depends(get_db)):
    validate_query_parameter_names(http_request, {"row_ids"})
    try:
        ids = json.loads(row_ids)
        if not isinstance(ids, list):
            fail("INPUT_LIMIT", 422)
    except (ValueError, TypeError):
        fail("INPUT_LIMIT", 422)
    return SourceRelationsResponse(status=200, message="ok", body=ImportSourceService(db).row_relations(import_file_id, ids))


@router.get("/import_file/{import_file_id}/row/{row_id}", response_model=SourceRowDetailResponse)
def row_detail(import_file_id: ResourceId, row_id: ResourceId, db: Session = Depends(get_db)):
    return SourceRowDetailResponse(status=200, message="ok", body=ImportSourceService(db).row_detail(import_file_id, row_id))
