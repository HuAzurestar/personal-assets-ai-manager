from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.schema.list_query import ListRequest, validate_list_capabilities
from backend.schema.response import ListResponse, SuccessResponse


class AiTaskRead(BaseModel):
    key: str
    version: int
    title: str
    input_schema: str
    output_schema: str
    prompt_key: str
    production_version: int


class AiPromptRead(BaseModel):
    id: int
    prompt_key: str
    version: int
    instruction: str
    note: str
    state: Literal["DRAFT", "PRODUCTION", "RETIRED"]
    created_time: datetime
    updated_time: datetime


class AiPromptCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt_key: str = Field(min_length=1, max_length=100)
    instruction: str = Field(min_length=1, max_length=16000)
    note: str = Field(default="", max_length=1000)

    @field_validator("instruction")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Prompt 内容不能为空")
        return value


class AiPromptPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_updated_time: datetime
    expected_production_version: int = Field(ge=1)

    @field_validator("expected_updated_time")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamp requires a timezone")
        return value


class AiInvocationRead(BaseModel):
    id: int
    task_key: str
    task_version: int
    prompt_key: str
    prompt_version: int
    model_id: int
    model_name: str
    run_id: str
    attempt: int
    status: Literal["STARTED", "SUCCEEDED", "REJECTED", "ERROR"]
    result_code: str
    error_code: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_tokens: int
    cost_usd: str
    created_time: datetime
    updated_time: datetime


class AiUsageRead(BaseModel):
    calls: int
    succeeded: int
    calls_with_usage: int
    reported_total_tokens: int
    calls_with_cost: int
    estimated_cost_usd: str
    cost_source: Literal["SDK_ESTIMATE"]


class AiListRequest(ListRequest):
    @model_validator(mode="after")
    def capabilities(self):
        validate_list_capabilities(self, query_fields=(), filter_operators={}, sorter_fields=())
        return self


class AiTaskListRequest(AiListRequest):
    pass


class AiPromptListRequest(AiListRequest):
    pass


class AiInvocationListRequest(AiListRequest):
    pass


class AiTaskListResponse(ListResponse[AiTaskRead]):
    pass


class AiPromptListResponse(ListResponse[AiPromptRead]):
    pass


class AiInvocationListResponse(ListResponse[AiInvocationRead]):
    pass


class AiPromptResponse(SuccessResponse[AiPromptRead]):
    pass


class AiUsageResponse(SuccessResponse[AiUsageRead]):
    pass
