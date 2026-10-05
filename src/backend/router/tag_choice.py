"""Public tag choices for filters; dictionary mutation remains View-owned."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from backend.router.bounded_query import bounded_list_dependency, bounded_search_dependency
from backend.router.dependency import ResourceId, get_db, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.tag_choice import (TagChoiceListRequest, TagChoiceSearchRequest,
    TagChoiceListResponse, TagChoiceSearchResponse, TagChoiceResponse)
from backend.service.tag_choice_service import TagChoiceService

router = APIRouter(
    prefix="/paam/tag/v1/tag",
    tags=["tag"],
    route_class=DomainErrorRoute,
)


@router.get("/list", response_model=TagChoiceListResponse)
def tag_list(request: TagChoiceListRequest = Depends(bounded_list_dependency(TagChoiceListRequest)),
             db: Session = Depends(get_db)):
    return TagChoiceListResponse(status=200, message="ok", body=TagChoiceService(db).page(request))


@router.get("/search", response_model=TagChoiceSearchResponse)
def tag_search(request: TagChoiceSearchRequest = Depends(bounded_search_dependency(TagChoiceSearchRequest)),
               db: Session = Depends(get_db)):
    return TagChoiceSearchResponse(status=200, message="ok", body=TagChoiceService(db).search(request))


@router.get("/{tag_id}", response_model=TagChoiceResponse)
def tag_get(tag_id: ResourceId, http_request: Request, db: Session = Depends(get_db)):
    validate_query_parameter_names(http_request, set())
    return TagChoiceResponse(status=200, message="ok", body=TagChoiceService(db).get(tag_id))
