from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.router.dependency import ResourceId, get_db
from backend.router.bounded_query import bounded_list_dependency, bounded_search_dependency
from backend.router.error import DomainErrorRoute
from backend.schema.review_command import PositionDraft
from backend.schema.position_read import (PositionMetadata, PositionResponse, PositionListResponse,
                                         PositionSearchResponse, PositionLegListResponse,
                                         PositionListRequest, PositionSearchRequest, PositionLegListRequest,
                                         PositionLegSearchRequest, PositionLegSearchResponse)
from backend.service.position_service import PositionService

router = APIRouter(
    prefix="/paam/financial/v1/position",
    tags=["position"],
    route_class=DomainErrorRoute,
)


position_list_request = bounded_list_dependency(PositionListRequest)
position_search_request = bounded_search_dependency(PositionSearchRequest)
position_leg_list_request = bounded_list_dependency(PositionLegListRequest)


@router.get("/list", response_model=PositionListResponse)
def position_list(request: PositionListRequest = Depends(position_list_request), db: Session = Depends(get_db)):
    return PositionListResponse(status=200, message="ok", body=PositionService(db).page(request))


@router.get("/search", response_model=PositionSearchResponse)
def position_search(request: PositionSearchRequest = Depends(position_search_request), db: Session = Depends(get_db)):
    return PositionSearchResponse(status=200, message="ok", body=PositionService(db).search(request))


@router.post("", response_model=PositionResponse)
def position_create(payload: PositionDraft, db: Session = Depends(get_db)):
    return PositionResponse(status=200, message="ok", body=PositionService(db).create(payload))


@router.put("/{position_id}/metadata", response_model=PositionResponse)
def position_metadata(position_id: ResourceId, payload: PositionMetadata, db: Session = Depends(get_db)):
    return PositionResponse(status=200, message="ok", body=PositionService(db).update(position_id, payload))


@router.get("/{position_id}/leg/list", response_model=PositionLegListResponse)
def position_legs(position_id: ResourceId, request: PositionLegListRequest = Depends(position_leg_list_request), db: Session = Depends(get_db)):
    return PositionLegListResponse(status=200, message="ok", body=PositionService(db).legs(position_id, request))


@router.get("/{position_id}/leg/search", response_model=PositionLegSearchResponse)
def position_leg_search(position_id: ResourceId,
    request: PositionLegSearchRequest = Depends(bounded_search_dependency(PositionLegSearchRequest)), db: Session = Depends(get_db)):
    return PositionLegSearchResponse(status=200, message="ok", body=PositionService(db).leg_search(position_id, request))


@router.get("/{position_id}", response_model=PositionResponse)
def position_detail(position_id: ResourceId, db: Session = Depends(get_db)):
    return PositionResponse(status=200, message="ok", body=PositionService(db).get(position_id))
