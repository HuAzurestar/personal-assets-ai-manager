from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


BodyT = TypeVar("BodyT")
ItemT = TypeVar("ItemT")


class Response(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: int
    message: str


class ResponseWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict[str, Any] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


class SuccessResponse(Response, Generic[BodyT]):
    status: Literal[200]
    message: Literal["ok"]
    body: BodyT
    warnings: list[ResponseWarning] = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
    )


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    details: Any | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


class ErrorResponse(Response):
    body: ErrorBody

    @field_validator("status")
    @classmethod
    def reject_success_status(cls, value: int) -> int:
        if value == 200:
            raise ValueError("an error response cannot use status 200")
        return value


class ListBody(BaseModel, Generic[ItemT]):
    model_config = ConfigDict(extra="forbid")

    items: list[ItemT]
    total: int = Field(ge=0)
    page_index: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class ListResponse(SuccessResponse[ListBody[ItemT]], Generic[ItemT]):
    pass
