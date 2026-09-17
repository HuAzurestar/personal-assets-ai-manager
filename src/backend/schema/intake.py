from __future__ import annotations

from pydantic import BaseModel, Field

from backend.schema.response import ListResponse, SuccessResponse
from backend.schema.target_review import (
    TargetFactConflictListBody,
    TargetReviewCaseRead,
)


class IntakeUploadFileRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=35_000_000)
    source_type: str | None = None
    password: str | None = Field(default=None, max_length=256)


class IntakePreviewRequest(BaseModel):
    files: list[IntakeUploadFileRequest] = Field(min_length=1, max_length=100)


class IntakeReviseRequest(BaseModel):
    # The key is the detected account identity; the value is the selected account identity.
    accounts: dict[str, str] = Field(default_factory=dict)
    decisions: dict[str, str] = Field(default_factory=dict)


class IntakeConfirmRequest(BaseModel):
    version: str = Field(min_length=1, max_length=128)


class ImportResponse(
    SuccessResponse[dict[str, object] | list[dict[str, object]]]
):
    body: dict[str, object] | list[dict[str, object]]


class ImportFactConflictResponse(SuccessResponse[TargetReviewCaseRead]):
    body: TargetReviewCaseRead


class ImportFactConflictListResponse(ListResponse[TargetReviewCaseRead]):
    body: TargetFactConflictListBody
