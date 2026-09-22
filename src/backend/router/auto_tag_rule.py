from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from backend.router.dependency import get_db, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.auto_tag_rule import (
    AutoTagCandidatePreviewResponse,
    AutoTagRuleCreateRequest,
    AutoTagRuleListRequest,
    AutoTagRuleListResponse,
    AutoTagRuleResponse,
    AutoTagRuleSummaryResponse,
    AutoTagRuleUpdateRequest,
)
from backend.schema.list_query import parse_list_request
from backend.service.auto_tag_rule_service import AutoTagRuleService


router = APIRouter(
    prefix="/paam/tag/v1/auto_rule",
    tags=["auto-tag-rule"],
    route_class=DomainErrorRoute,
)


@router.post("", response_model=AutoTagRuleResponse)
def create_rule(
    payload: AutoTagRuleCreateRequest,
    db: Session = Depends(get_db),
):
    return AutoTagRuleResponse(
        status=200,
        message="ok",
        body=AutoTagRuleService(db).create(payload),
    )


@router.get("/list", response_model=AutoTagRuleListResponse)
def list_rules(
    http_request: Request,
    page_index: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    query: str | None = Query(default=None),
    filter: str | None = Query(default=None),
    sorter: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    validate_query_parameter_names(
        http_request,
        {"page_index", "page_size", "query", "filter", "sorter"},
    )
    request = parse_list_request(
        AutoTagRuleListRequest,
        page_index=page_index,
        page_size=page_size,
        query=query,
        filter=filter,
        sorter=sorter,
    )
    return AutoTagRuleListResponse(
        status=200,
        message="ok",
        body=AutoTagRuleService(db).list(request),
    )


@router.get("/{rule_id}", response_model=AutoTagRuleResponse)
def get_rule(rule_id: int, db: Session = Depends(get_db)):
    return AutoTagRuleResponse(
        status=200,
        message="ok",
        body=AutoTagRuleService(db).get(rule_id),
    )


@router.put("/{rule_id}", response_model=AutoTagRuleResponse)
def update_rule(
    rule_id: int,
    payload: AutoTagRuleUpdateRequest,
    db: Session = Depends(get_db),
):
    return AutoTagRuleResponse(
        status=200,
        message="ok",
        body=AutoTagRuleService(db).update(rule_id, payload),
    )


@router.get("/{rule_id}/summary", response_model=AutoTagRuleSummaryResponse)
def get_rule_summary(rule_id: int, db: Session = Depends(get_db)):
    return AutoTagRuleSummaryResponse(
        status=200,
        message="ok",
        body=AutoTagRuleService(db).summary(rule_id),
    )


@router.post(
    "/{rule_id}/candidate_preview",
    response_model=AutoTagCandidatePreviewResponse,
)
def preview_candidates(rule_id: int, db: Session = Depends(get_db)):
    return AutoTagCandidatePreviewResponse(
        status=200,
        message="ok",
        body=AutoTagRuleService(db).candidate_preview(rule_id),
    )
