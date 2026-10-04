from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from backend.middleware.schema.ai import (
    AiInvocationListRequest, AiInvocationListResponse,
    AiPromptCreateRequest, AiPromptListRequest, AiPromptListResponse,
    AiPromptPublishRequest, AiPromptResponse,
    AiTaskListRequest, AiTaskListResponse, AiUsageResponse,
)
from backend.router.dependency import get_db, get_middleware, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.schema.disclosure_preview import DisclosurePreviewRequest, DisclosurePreviewResponse
from backend.schema.list_query import parse_list_request

router = APIRouter(
    prefix="/paam/system/v1/ai",
    tags=["system-ai"],
    route_class=DomainErrorRoute,
)


def _list_request(http_request, model, page_index, page_size, query, filter, sorter):
    validate_query_parameter_names(http_request, {"page_index", "page_size", "query", "filter", "sorter"})
    return parse_list_request(model, page_index=page_index, page_size=page_size,
                              query=query, filter=filter, sorter=sorter)


@router.get("/task/list", response_model=AiTaskListResponse)
def tasks(http_request: Request, page_index: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
          query: str | None = None, filter: str | None = None, sorter: str | None = None,
          db: Session = Depends(get_db), middleware=Depends(get_middleware)):
    request = _list_request(http_request, AiTaskListRequest, page_index, page_size, query, filter, sorter)
    return AiTaskListResponse(status=200, message="ok", body=middleware.management(db).tasks(request))


@router.get("/prompt/list", response_model=AiPromptListResponse)
def prompts(http_request: Request, page_index: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
            query: str | None = None, filter: str | None = None, sorter: str | None = None,
            db: Session = Depends(get_db), middleware=Depends(get_middleware)):
    request = _list_request(http_request, AiPromptListRequest, page_index, page_size, query, filter, sorter)
    return AiPromptListResponse(status=200, message="ok", body=middleware.prompt(db).list(request))


@router.post("/prompt", response_model=AiPromptResponse)
def create_prompt(payload: AiPromptCreateRequest, db: Session = Depends(get_db),
                  middleware=Depends(get_middleware)):
    return AiPromptResponse(status=200, message="ok", body=middleware.prompt(db).create(payload))


@router.post("/prompt/{prompt_id}/publish", response_model=AiPromptResponse)
def publish_prompt(payload: AiPromptPublishRequest, prompt_id: int,
                   db: Session = Depends(get_db), middleware=Depends(get_middleware)):
    return AiPromptResponse(status=200, message="ok", body=middleware.prompt(db).publish(prompt_id, payload))


@router.post("/prompt/{prompt_id}/preview", response_model=DisclosurePreviewResponse)
def preview_prompt(payload: DisclosurePreviewRequest, prompt_id: int,
                   db: Session = Depends(get_db), middleware=Depends(get_middleware)):
    return DisclosurePreviewResponse(status=200, message="ok",
                                     body=middleware.management(db).preview(prompt_id, payload))


@router.get("/invocation/list", response_model=AiInvocationListResponse)
def invocations(http_request: Request, page_index: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                query: str | None = None, filter: str | None = None, sorter: str | None = None,
                middleware=Depends(get_middleware)):
    request = _list_request(http_request, AiInvocationListRequest, page_index, page_size, query, filter, sorter)
    return AiInvocationListResponse(status=200, message="ok", body=middleware.ai.invocations.list(request))


@router.get("/usage", response_model=AiUsageResponse)
def usage(middleware=Depends(get_middleware)):
    return AiUsageResponse(status=200, message="ok", body=middleware.ai.invocations.usage())
