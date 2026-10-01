from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.router.error import DomainErrorRoute
from backend.router.dependency import get_db
from backend.router.bounded_query import bounded_list_dependency, bounded_search_dependency
from backend.schema.candidate import (CandidateListResponse, CandidateSearchResponse,
                                      CandidateListRequest, CandidateSearchRequest)
from backend.service.candidate_service import CandidateService

router = APIRouter(
    prefix="/paam/ledger/v1/candidate",
    tags=["review-candidate"],
    route_class=DomainErrorRoute,
)

candidate_list_request = bounded_list_dependency(CandidateListRequest)
candidate_search_request = bounded_search_dependency(CandidateSearchRequest)


@router.get("/list", response_model=CandidateListResponse)
def candidate_list(request: CandidateListRequest = Depends(candidate_list_request), db: Session = Depends(get_db)):
    return CandidateListResponse(status=200, message="ok", body=CandidateService(db).page(request))


@router.get("/search", response_model=CandidateSearchResponse)
def candidate_search(request: CandidateSearchRequest = Depends(candidate_search_request), db: Session = Depends(get_db)):
    return CandidateSearchResponse(status=200, message="ok", body=CandidateService(db).search(request))
