from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.schema.list_query import ListSorter


class ReviewCaseListItem(BaseModel):
    id: int
    behavior_type: int
    status: int
    title: str
    created_time: datetime
    updated_time: datetime


class ReviewCaseFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    behavior_type: int | None = None
    status: int | None = None


class ReviewCaseSorter(ListSorter):
    field: Literal["id", "created_time", "updated_time"] = "updated_time"


class ReviewCasePageRead(BaseModel):
    items: list[ReviewCaseListItem]
    total: int
    page: int
    page_size: int
    q: str
    filter: ReviewCaseFilter
    sorter: ReviewCaseSorter


class ReviewCasePageResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: ReviewCasePageRead
