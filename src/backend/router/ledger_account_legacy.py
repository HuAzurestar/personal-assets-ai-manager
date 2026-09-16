"""Legacy Fact-owned Account Review routes pending caller migration."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.router.dependency import get_db
from backend.router.error import DomainErrorRoute
from backend.schema.target_review import (
    TargetAccountSetRequest,
    TargetReviewCaseResponse,
    TargetReviewTransitionRequest,
)
from backend.service.target_account_service import TargetAccountService


router = APIRouter(
    prefix="/paam/review/v1",
    tags=["target-review"],
    route_class=DomainErrorRoute,
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
