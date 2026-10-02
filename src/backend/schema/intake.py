from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from backend.schema.import_command import ImportConfirmInput, ImportReviseInput

from backend.schema.response import ListResponse, SuccessResponse
from backend.schema.target_review import (
    TargetFactConflictListBody,
    TargetReviewCaseRead,
)


class IntakeUploadFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=27962028)
    source_type: str | None = None
    password: str | None = Field(default=None, max_length=256)


class IntakePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: list[IntakeUploadFileRequest] = Field(min_length=1, max_length=100)
    timezone: str = Field(default="Asia/Hong_Kong", min_length=1, max_length=120)


# Python entry-name compatibility only. HTTP now rejects old accounts/decisions
# and version fields; there is no legacy whole-plan writer or replay path.
IntakeReviseRequest = ImportReviseInput
IntakeConfirmRequest = ImportConfirmInput


class ImportResponse(
    SuccessResponse[dict[str, object] | list[dict[str, object]]]
):
    body: dict[str, object] | list[dict[str, object]]


class ImportFactConflictResponse(SuccessResponse[TargetReviewCaseRead]):
    body: TargetReviewCaseRead


class ImportFactConflictListResponse(ListResponse[TargetReviewCaseRead]):
    body: TargetFactConflictListBody
