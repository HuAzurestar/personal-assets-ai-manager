from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.schema.list_query import ListSorter
from backend.schema.transaction_fact import (
    TransactionFactFilter,
    TransactionFactListItem,
    TransactionFactSorter,
)


class ImportFileFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str | None = None
    institution_code: str | None = None
    file_format: str | None = None
    status: str | None = None


class ImportFileSorter(ListSorter):
    field: Literal["id", "filename", "created_time", "updated_time"] = "created_time"


class ImportFileRead(BaseModel):
    id: int
    batch_code: str
    source_type: str
    institution_code: str
    filename: str
    file_format: str
    sha256: str
    period_start: str
    period_end: str
    total_count: int
    success_count: int
    skip_count: int
    issue_count: int
    status: str
    created_time: datetime
    updated_time: datetime


class ImportFilePageRead(BaseModel):
    items: list[ImportFileRead]
    total: int
    page: int
    page_size: int
    q: str
    filter: ImportFileFilter
    sorter: ImportFileSorter


class ImportFilePageResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: ImportFilePageRead


class ImportFileSummaryRead(BaseModel):
    import_file_count: int
    imported_file_count: int
    row_count: int
    success_count: int
    skip_count: int
    issue_count: int
    q: str
    filter: ImportFileFilter


class ImportFileSummaryResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: ImportFileSummaryRead


class ImportFileDetailRead(BaseModel):
    import_file: ImportFileRead


class ImportFileDetailResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: ImportFileDetailRead


class ImportFileTransactionFactPageRead(BaseModel):
    items: list[TransactionFactListItem]
    total: int
    page: int
    page_size: int
    q: str
    filter: TransactionFactFilter
    sorter: TransactionFactSorter


class ImportFileTransactionFactPageResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: ImportFileTransactionFactPageRead
