from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.errors import ReviewCommandError
from app.schemas import CandidateBatchDecision, CandidatePageRead, ReviewCandidateRead
from app.schemas.review import ReviewPageQuery
from app.services.review_service import ReviewService


router = APIRouter(prefix="/api/candidates", tags=["review"])


@router.get("", response_model=list[ReviewCandidateRead])
def list_candidates(db: Session = Depends(get_db)):
    return ReviewService(db).all()


@router.get("/page", response_model=CandidatePageRead)
def page_candidates(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    candidate_type: str | None = None,
    db: Session = Depends(get_db),
):
    return ReviewService(db).page(ReviewPageQuery(
        page=page,
        page_size=page_size,
        status=status,
        candidate_type=candidate_type,
    ))


@router.post("/batch", response_model=list[ReviewCandidateRead])
def decide_candidates_batch(
    payload: CandidateBatchDecision,
    db: Session = Depends(get_db),
):
    try:
        return ReviewService(db).decide_batch(payload)
    except ReviewCommandError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
