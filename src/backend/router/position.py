from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from backend.router.dependency import get_db, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.review_command import PositionDraft
from backend.schema.position_read import (PositionMetadata, PositionResponse, PositionListResponse,
                                         PositionSearchResponse, PositionLegListResponse)
from backend.schema.list_query import ListRequest, parse_list_request
from backend.schema.bounded_search import SearchRequest, parse_search_request
from backend.service.position_service import PositionService

router = APIRouter(prefix="/paam/financial/v1/position", tags=["position"], route_class=DomainErrorRoute)


def envelope(body):
    return dict(status=200, message="ok", body=body)


def position_list_request(http_request: Request, page_index: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100), filter: str | None = None, sorter: str | None = None):
    validate_query_parameter_names(http_request, {"page_index", "page_size", "filter", "sorter"})
    return parse_list_request(ListRequest, page_index=page_index, page_size=page_size, filter=filter, sorter=sorter)


def position_search_request(http_request: Request, page_size: int = Query(20, ge=1, le=100),
    query: str | None = None, filter: str | None = None, sorter: str | None = None, cursor: str | None = None):
    validate_query_parameter_names(http_request, {"page_size", "query", "filter", "sorter", "cursor"})
    return parse_search_request(page_size=page_size, query=query, filter=filter, sorter=sorter, cursor=cursor)


@router.get("/list", response_model=PositionListResponse)
def position_list(request: ListRequest = Depends(position_list_request), db: Session = Depends(get_db)):
    return envelope(PositionService(db).page(request))


@router.get("/search", response_model=PositionSearchResponse)
def position_search(request: SearchRequest = Depends(position_search_request), db: Session = Depends(get_db)):
    return envelope(PositionService(db).search(request))


@router.post("", response_model=PositionResponse)
def position_create(payload: PositionDraft, db: Session = Depends(get_db)):
    return envelope(PositionService(db).create(payload))


@router.put("/{position_id}/metadata", response_model=PositionResponse)
def position_metadata(position_id: int, payload: PositionMetadata, db: Session = Depends(get_db)):
    return envelope(PositionService(db).update(position_id, payload))


@router.get("/{position_id}/leg/list", response_model=PositionLegListResponse)
def position_legs(position_id: int, request: ListRequest = Depends(position_list_request), db: Session = Depends(get_db)):
    return envelope(PositionService(db).legs(position_id, request))


@router.get("/{position_id}", response_model=PositionResponse)
def position_detail(position_id: int, db: Session = Depends(get_db)):
    return envelope(PositionService(db).get(position_id))
