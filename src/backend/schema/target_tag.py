from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schema.list_query import ListSorter


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


class TargetTagAssignmentRequest(BaseModel):
    """The complete effective Tag state owned directly by one Ledger."""

    model_config = ConfigDict(extra="forbid")

    tag_state: dict[str, str]


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


class TargetTagViewFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ACTIVE", "ARCHIVED"] | None = None
    include_archived: bool = False


class TargetTagViewSorter(ListSorter):
    field: Literal["id", "name", "system_name", "created_time", "updated_time"] = "id"


class TargetTagViewPageRead(BaseModel):
    items: list[TargetTagViewRead]
    total: int
    page: int
    page_size: int
    q: str
    filter: TargetTagViewFilter
    sorter: TargetTagViewSorter


class TargetTagViewResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: TargetTagViewRead


class TargetTagViewListResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: TargetTagViewPageRead


class TargetTagAssignmentRead(BaseModel):
    ledger_id: int
    tag_state: dict[str, str]


class TargetTagAssignmentResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: TargetTagAssignmentRead
