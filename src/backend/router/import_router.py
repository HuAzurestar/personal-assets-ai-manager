"""Single v1 import adapter: explicit choices and selected-row confirmation."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Literal
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from backend.router.dependency import get_db, validate_query_parameter_names
from backend.router.error import DomainErrorRoute
from backend.error import TargetIntakeError
from backend.schema.intake import IntakePreviewRequest
from backend.schema.import_command import (ImportConfirmInput, ImportConfirmPreviewInput, ImportReviseInput,
                                          PreviewRowListRequest, ImportMatchListRequest, ImportOperationPreviewInput,
                                          ImportBindingPreviewInput)
from backend.schema.identifier import SQLITE_ID_MAX
from backend.schema.import_batch_read import (ImportPreviewResponse, PreviewRowListResponse,
                                            ImportConfirmResponse, ImportCancelResponse, ImportBatchPreviewResponse,
                                            ImportMatchListResponse, ImportOperationPreviewResponse, ImportBindingPreviewResponse)
from backend.schema.list_query import parse_list_request
from backend.service.target_intake_service import TargetIntakeService

router = APIRouter(prefix="/paam/import/v1", tags=["import"], route_class=DomainErrorRoute)


@router.post("/preview", response_model=ImportPreviewResponse)
def preview(payload: IntakePreviewRequest, db: Session = Depends(get_db)):
    try:
        timezone = ZoneInfo(payload.timezone)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise TargetIntakeError(422, "invalid IANA timezone", code="INVALID_TIME") from error
    return ImportPreviewResponse(status=200, message="ok",
        body=TargetIntakeService(db).preview(payload, source_timezone=timezone))


@router.get("/preview/{token}", response_model=ImportPreviewResponse)
def current(token: str, db: Session = Depends(get_db)):
    return ImportPreviewResponse(status=200, message="ok", body=TargetIntakeService(db).current(token))


@router.put("/preview/{token}", response_model=ImportPreviewResponse)
def revise(token: str, payload: ImportReviseInput, db: Session = Depends(get_db)):
    return ImportPreviewResponse(status=200, message="ok", body=TargetIntakeService(db).revise(token, payload))


@router.get("/preview/{token}/row/list", response_model=PreviewRowListResponse)
def rows(token: str, http_request: Request, preview_digest: str = Query(pattern=r"^[0-9a-f]{64}$"),
         page_index: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
         query: str | None = None, filter: str | None = None, sorter: str | None = None,
         db: Session = Depends(get_db)):
    validate_query_parameter_names(http_request, {"preview_digest", "page_index", "page_size", "query", "filter", "sorter"})
    request = parse_list_request(PreviewRowListRequest, page_index=page_index, page_size=page_size,
                                 query=query, filter=filter, sorter=sorter)
    return PreviewRowListResponse(status=200, message="ok", body=TargetIntakeService(db).row_page(token, preview_digest, request))


@router.post("/preview/{token}/confirm", response_model=ImportConfirmResponse)
def confirm(token: str, payload: ImportConfirmInput, db: Session = Depends(get_db)):
    return ImportConfirmResponse(status=200, message="ok", body=TargetIntakeService(db).confirm(token, payload))


@router.get("/preview/{token}/match/list", response_model=ImportMatchListResponse)
def matches(token: str, http_request: Request, preview_digest: str = Query(pattern=r"^[0-9a-f]{64}$"),
            file_id: int = Query(ge=1, le=SQLITE_ID_MAX), source_row_number: int = Query(ge=1, le=SQLITE_ID_MAX),
            kind: Literal["SAME_SOURCE", "CROSS_SOURCE"] = Query(),
            page_index: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
            db: Session = Depends(get_db)):
    validate_query_parameter_names(http_request, {"preview_digest", "file_id", "source_row_number", "kind", "page_index", "page_size"})
    request = ImportMatchListRequest(page_index=page_index, page_size=page_size)
    return ImportMatchListResponse(status=200, message="ok", body=TargetIntakeService(db).match_page(token, preview_digest,
        (file_id, source_row_number), kind, request))


@router.post("/preview/{token}/confirm-preview", response_model=ImportBatchPreviewResponse)
def confirm_preview(token: str, payload: ImportConfirmPreviewInput, db: Session = Depends(get_db)):
    return ImportBatchPreviewResponse(status=200, message="ok", body=TargetIntakeService(db).confirm_preview(token, payload))


@router.post("/preview/{token}/operation-preview", response_model=ImportOperationPreviewResponse)
def operation_preview(token: str, payload: ImportOperationPreviewInput, db: Session = Depends(get_db)):
    return ImportOperationPreviewResponse(status=200, message="ok", body=TargetIntakeService(db).operation_preview(token, payload))


@router.delete("/preview/{token}", response_model=ImportCancelResponse)
def cancel(token: str, db: Session = Depends(get_db)):
    return ImportCancelResponse(status=200, message="ok", body=TargetIntakeService(db).cancel(token))


@router.post("/preview/{token}/binding-preview", response_model=ImportBindingPreviewResponse)
def binding_preview(token: str, payload: ImportBindingPreviewInput, db: Session = Depends(get_db)):
    return ImportBindingPreviewResponse(status=200, message="ok", body=TargetIntakeService(db).binding_preview(token, payload))
