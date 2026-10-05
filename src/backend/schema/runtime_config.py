from typing import Literal
from pydantic import BaseModel, JsonValue
from backend.schema.response import SuccessResponse


class ConfigSectionRead(BaseModel):
    section: str
    effective_value: JsonValue
    effective_origin: Literal["deployment", "persisted", "default"]
    effective_token: str
    storage_token: str | None
    overridden: bool


class ConfigApplyRead(BaseModel):
    desired_token: str | None
    applied_token: str | None
    status: Literal["PENDING", "APPLIED", "FAILED", "NEEDS_RESTART"]
    available: bool
    error_code: str | None


class RuntimeConfigRead(BaseModel):
    sections: list[ConfigSectionRead]
    apply: ConfigApplyRead


class RuntimeConfigResponse(SuccessResponse[RuntimeConfigRead]):
    pass
