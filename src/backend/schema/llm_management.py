from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from backend.schema.response import SuccessResponse


class PromptRead(BaseModel):
    id: str
    name: str
    fingerprint: str
    read_only: Literal[True] = True
    messages: list[dict[str, str]] | None = Field(default=None, exclude_if=lambda v: v is None)


class PromptListResponse(SuccessResponse[list[PromptRead]]):
    pass


class PromptResponse(SuccessResponse[PromptRead]):
    pass


class PromptReferenceRead(BaseModel):
    rule_id: int
    name: str


class PromptReferenceResponse(SuccessResponse[list[PromptReferenceRead]]):
    pass


class PromptPreviewRead(BaseModel):
    synthetic: Literal[True] = True
    prompt_id: str
    fingerprint: str
    messages: list[dict[str, str]]


class PromptPreviewResponse(SuccessResponse[PromptPreviewRead]):
    pass


class LlmCallRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    source: str
    operation_id: str | None
    run_id: str
    prompt_id: str | None
    prompt_fingerprint: str | None
    model_id: int | None
    status: str
    error_code: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    estimated_cost: str | None
    created_time: datetime
    updated_time: datetime


class LlmCallPageRead(BaseModel):
    items: list[LlmCallRead]
    total: int
    page_index: int
    page_size: int


class LlmCallPageResponse(SuccessResponse[LlmCallPageRead]):
    pass


class LlmCallStatsRead(BaseModel):
    call_count: int
    succeeded_count: int
    failed_count: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


class LlmCallStatsResponse(SuccessResponse[LlmCallStatsRead]):
    pass


class LlmCallDetailRead(LlmCallRead):
    request_json: str
    response_text: str
    response_truncated: bool
    metadata_json: str


class LlmCallDetailResponse(SuccessResponse[LlmCallDetailRead]):
    pass
