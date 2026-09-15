from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.target_review import (
    TargetAccountSetRequest,
    TargetEconomicReviewCreateRequest,
    TargetEconomicReviewPageResponse,
    TargetEconomicReviewResponse,
    TargetEconomicReviewUpdateRequest,
    TargetFactAllocationCandidateResponse,
    TargetFactConflictResolveRequest,
    TargetReviewCaseListResponse,
    TargetReviewCasePageResponse,
    TargetReviewCaseResponse,
    TargetReviewCreateRequest,
    TargetReviewTransitionRequest,
    TargetReviewUpdateRequest,
)
from backend.service.target_account_service import TargetAccountService
from backend.service.target_fact_conflict_service import TargetFactConflictService
from backend.service.target_economic_service import TargetEconomicService
from backend.service.target_review_service import TargetReviewService


router = APIRouter(
    prefix="/paam/review/v1",
    tags=["target-review"],
    route_class=DomainErrorRoute,
)
v2_router = APIRouter(
    prefix="/paam/review/v2",
    tags=["economic-review"],
    route_class=DomainErrorRoute,
)


@v2_router.post("/case/create", response_model=TargetEconomicReviewResponse)
def create_economic_case(
    payload: TargetEconomicReviewCreateRequest,
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewResponse(body=TargetEconomicService(db).create(payload))


@v2_router.post("/case/confirm/{case_id}", response_model=TargetEconomicReviewResponse)
def confirm_economic_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewResponse(
        body=TargetEconomicService(db).confirm(case_id, payload)
    )


@v2_router.put("/case/update/{case_id}", response_model=TargetEconomicReviewResponse)
def update_economic_case(
    case_id: int,
    payload: TargetEconomicReviewUpdateRequest,
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewResponse(
        body=TargetEconomicService(db).update(case_id, payload)
    )


@v2_router.post("/case/revoke/{case_id}", response_model=TargetEconomicReviewResponse)
def revoke_economic_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewResponse(
        body=TargetEconomicService(db).revoke(case_id, payload)
    )


@v2_router.post("/case/restore/{case_id}", response_model=TargetEconomicReviewResponse)
def restore_economic_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewResponse(
        body=TargetEconomicService(db).confirm(case_id, payload, restore=True)
    )


@v2_router.get("/case/detail/{case_id}", response_model=TargetEconomicReviewResponse)
def economic_case_detail(case_id: int, db: Session = Depends(get_db)):
    return TargetEconomicReviewResponse(body=TargetEconomicService(db).detail(case_id))


@v2_router.get("/case/page", response_model=TargetEconomicReviewPageResponse)
def economic_case_page(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    status: str = Query(default="", pattern="^(|PENDING|CONFIRMED|REVOKED)$"),
    db: Session = Depends(get_db),
):
    return TargetEconomicReviewPageResponse(
        body=TargetEconomicService(db).page(page, page_size, status)
    )


@v2_router.get("/fact/candidates", response_model=TargetFactAllocationCandidateResponse)
def economic_fact_candidates(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return TargetFactAllocationCandidateResponse(
        body=TargetEconomicService(db).fact_candidates(limit)
    )


@router.post("/case/create", response_model=TargetReviewCaseResponse)
def create_case(
    payload: TargetReviewCreateRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(body=TargetReviewService(db).create(payload))


@router.post("/case/confirm/{case_id}", response_model=TargetReviewCaseResponse)
def confirm_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(body=TargetReviewService(db).confirm(case_id, payload))


@router.put("/case/update/{case_id}", response_model=TargetReviewCaseResponse)
def update_case(
    case_id: int,
    payload: TargetReviewUpdateRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(body=TargetReviewService(db).update(case_id, payload))


@router.post("/case/revoke/{case_id}", response_model=TargetReviewCaseResponse)
def revoke_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(body=TargetReviewService(db).revoke(case_id, payload))


@router.post("/case/restore/{case_id}", response_model=TargetReviewCaseResponse)
def restore_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(
        body=TargetReviewService(db).confirm(case_id, payload, restore=True)
    )


@router.post("/case/dismiss/{case_id}", response_model=TargetReviewCaseResponse)
def dismiss_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(body=TargetReviewService(db).dismiss(case_id, payload))


@router.post("/case/reopen/{case_id}", response_model=TargetReviewCaseResponse)
def reopen_case(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(
        body=TargetReviewService(db).dismiss(case_id, payload, reopen=True)
    )


@router.get("/case/detail/{case_id}", response_model=TargetReviewCaseResponse)
def case_detail(case_id: int, db: Session = Depends(get_db)):
    return TargetReviewCaseResponse(body=TargetReviewService(db).detail(case_id))


@router.get("/case/list", response_model=TargetReviewCaseListResponse)
def case_list(
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return TargetReviewCaseListResponse(body=TargetReviewService(db).list(limit))


@router.get("/case/page", response_model=TargetReviewCasePageResponse)
def case_page(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    status: str = Query(default="", pattern="^(|PENDING|CONFIRMED|REJECTED|REVOKED)$"),
    review_type: str = Query(
        default="",
        pattern="^(|CLASSIFICATION|AA|LOAN_BORROW|LOAN_LEND|REFUND|TRANSFER|FX_EXCHANGE|DUPLICATE|TAG|ACCOUNT|FACT_CONFLICT)$",
    ),
    db: Session = Depends(get_db),
):
    return TargetReviewCasePageResponse(
        body=TargetReviewService(db).page(page, page_size, status, review_type)
    )


@router.put("/account/set/{fact_id}", response_model=TargetReviewCaseResponse)
def set_account(
    fact_id: int,
    payload: TargetAccountSetRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(body=TargetAccountService(db).set(fact_id, payload))


@router.post("/account/revoke/{case_id}", response_model=TargetReviewCaseResponse)
def revoke_account(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(body=TargetAccountService(db).revoke(case_id, payload))


@router.post("/account/restore/{case_id}", response_model=TargetReviewCaseResponse)
def restore_account(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(
        body=TargetAccountService(db).revoke(case_id, payload, restore=True)
    )


@router.post("/fact-conflict/resolve/{case_id}", response_model=TargetReviewCaseResponse)
def resolve_fact_conflict(
    case_id: int,
    payload: TargetFactConflictResolveRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(
        body=TargetFactConflictService(db).resolve(case_id, payload)
    )


@router.post("/fact-conflict/dismiss/{case_id}", response_model=TargetReviewCaseResponse)
def dismiss_fact_conflict(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(
        body=TargetFactConflictService(db).dismiss(case_id, payload)
    )


@router.post("/fact-conflict/reopen/{case_id}", response_model=TargetReviewCaseResponse)
def reopen_fact_conflict(
    case_id: int,
    payload: TargetReviewTransitionRequest,
    db: Session = Depends(get_db),
):
    return TargetReviewCaseResponse(
        body=TargetFactConflictService(db).reopen(case_id, payload)
    )
