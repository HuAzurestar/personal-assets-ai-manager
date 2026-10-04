from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session
from backend.core.target_database import SessionLocal
from backend.router.error import DomainErrorRoute
from backend.schema.llm_management import (
    PromptListResponse, PromptResponse, PromptReferenceResponse, PromptPreviewResponse,
    LlmCallPageResponse, LlmCallStatsResponse, LlmCallDetailResponse,
)
from backend.service.llm_management_service import LlmManagementService

router = APIRouter(prefix="/paam/system/v1/llm", tags=["llm-management"], route_class=DomainErrorRoute)


def get_db():
    with SessionLocal() as db:
        yield db


@router.get("/prompt/list", response_model=PromptListResponse)
def prompts(db: Session = Depends(get_db)):
    return PromptListResponse(status=200, message="ok", body=LlmManagementService(db).prompts())


@router.get("/prompt/{prompt_id}", response_model=PromptResponse)
def prompt(prompt_id: str, db: Session = Depends(get_db)):
    return PromptResponse(status=200, message="ok", body=LlmManagementService(db).prompt(prompt_id))


@router.get("/prompt/{prompt_id}/reference", response_model=PromptReferenceResponse)
def references(prompt_id: str, db: Session = Depends(get_db)):
    return PromptReferenceResponse(status=200, message="ok", body=LlmManagementService(db).references(prompt_id))


@router.post("/prompt/{prompt_id}/preview", response_model=PromptPreviewResponse)
def preview(prompt_id: str, db: Session = Depends(get_db)):
    return PromptPreviewResponse(status=200, message="ok", body=LlmManagementService(db).preview(prompt_id))


@router.get("/call/list", response_model=LlmCallPageResponse)
def calls(page_index: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
          db: Session = Depends(get_db)):
    return LlmCallPageResponse(status=200, message="ok", body=LlmManagementService(db).calls(page_index, page_size))


@router.get("/call/statistics", response_model=LlmCallStatsResponse)
def statistics(db: Session = Depends(get_db)):
    return LlmCallStatsResponse(status=200, message="ok", body=LlmManagementService(db).statistics())


@router.get("/call/{call_id}", response_model=LlmCallDetailResponse)
def detail(call_id: int, x_paam_audit_token: str | None = Header(default=None), db: Session = Depends(get_db)):
    return LlmCallDetailResponse(status=200, message="ok", body=LlmManagementService(db).detail(call_id, x_paam_audit_token))
