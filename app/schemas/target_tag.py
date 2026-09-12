from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TargetTagViewCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    system_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


class TargetTagCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    system_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


class TargetTagStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ACTIVE", "ARCHIVED"]


class TargetTagRead(BaseModel):
    id: int
    name: str
    system_name: str
    status: str


class TargetTagViewRead(BaseModel):
    id: int
    name: str
    system_name: str
    status: str
    tags: list[TargetTagRead]


class TargetTagViewResponse(BaseModel):
    status: Literal["success"] = "success"
    message: str = "ok"
    body: TargetTagViewRead


class TargetTagViewListResponse(BaseModel):
    status: Literal["success"] = "success"
    message: str = "ok"
    body: list[TargetTagViewRead]
