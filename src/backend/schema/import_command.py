"""Explicit bounded import choices, not a whole-plan version/replay contract."""
from datetime import datetime, timezone
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from backend.schema.list_query import ListRequest

Positive = Annotated[StrictInt, Field(gt=0)]


class ImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RowIdentity(ImportInput):
    file_id: Positive
    source_row_number: Positive


class RowChoice(RowIdentity):
    decision: Literal["ACCEPT", "SKIP"]
    recheck: bool = False
    account_ref_id: Annotated[StrictInt, Field(ge=0)] | None = None


class PreviewExpected(ImportInput):
    expected_updated_time: datetime

    @model_validator(mode="after")
    def aware_time(self):
        if self.expected_updated_time.tzinfo is None or self.expected_updated_time.utcoffset() is None:
            raise ValueError("expected_updated_time requires a timezone")
        self.expected_updated_time = self.expected_updated_time.astimezone(timezone.utc)
        return self


class ImportReviseInput(PreviewExpected):
    choices: list[RowChoice] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def distinct_rows(self):
        if len({(row.file_id, row.source_row_number) for row in self.choices}) != len(self.choices):
            raise ValueError("duplicate choice row")
        return self


class ImportConfirmInput(PreviewExpected):
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_rows: list[RowIdentity] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def distinct_rows(self):
        if len({(row.file_id, row.source_row_number) for row in self.selected_rows}) != len(self.selected_rows):
            raise ValueError("duplicate selected row")
        return self


class PreviewRowListRequest(ListRequest):
    pass
