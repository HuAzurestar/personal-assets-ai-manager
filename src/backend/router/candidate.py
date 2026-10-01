from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.router.error import DomainErrorRoute
from backend.router.dependency import get_db
from backend.router.position import position_list_request, position_search_request
from backend.schema.list_query import ListRequest
from backend.schema.bounded_search import SearchRequest
from backend.schema.candidate import CandidateListResponse, CandidateSearchResponse
from backend.service.candidate_service import CandidateService

router = APIRouter(prefix="/paam/ledger/v1/candidate", tags=["review-candidate"], route_class=DomainErrorRoute)


@router.get("/list", response_model=CandidateListResponse)
def candidate_list(request: ListRequest = Depends(position_list_request), db: Session = Depends(get_db)):
    return dict(status=200, message="ok", body=CandidateService(db).page(request))


@router.get("/search", response_model=CandidateSearchResponse)
def candidate_search(request: SearchRequest = Depends(position_search_request), db: Session = Depends(get_db)):
    return dict(status=200, message="ok", body=CandidateService(db).search(request))
