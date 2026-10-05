"""Explicit bounded import choices and ephemeral approval; no version/replay store."""
from datetime import datetime, timezone
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictBool, model_validator
from backend.schema.list_query import ListRequest
from backend.schema.identifier import PositiveId, NonnegativeId

Positive = PositiveId


class ImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RowIdentity(ImportInput):
    file_id: Positive
    source_row_number: Positive


class FactEvidenceTarget(ImportInput):
    kind: Literal["FACT"]
    transaction_id: Positive


class RowEvidenceTarget(RowIdentity):
    kind: Literal["ROW"]


EvidenceTarget = Annotated[FactEvidenceTarget | RowEvidenceTarget, Field(discriminator="kind")]


class RowChoice(RowIdentity):
    decision: Literal["ACCEPT", "SKIP"]
    recheck: StrictBool = False
    account_ref_id: NonnegativeId | None = None
    resolution: Literal["AUTO", "NEW", "LINK_EXISTING", "DUPLICATE"] = "AUTO"
    target: EvidenceTarget | None = None
    acknowledge_new_risk: StrictBool = False

    @model_validator(mode="after")
    def explicit_intent(self):
        paired = self.resolution in {"LINK_EXISTING", "DUPLICATE"}
        if paired != (self.target is not None):
            raise ValueError("only an explicit pair may carry a target, and a pair requires one")
        if self.decision == "SKIP" and (self.resolution != "AUTO" or self.acknowledge_new_risk):
            raise ValueError("SKIP cannot carry a financial resolution or risk consent")
        if self.acknowledge_new_risk and (self.decision != "ACCEPT" or self.resolution != "NEW"):
            raise ValueError("risk consent requires explicit NEW/ACCEPT")
        if self.resolution == "LINK_EXISTING" and self.account_ref_id is not None:
            raise ValueError("evidence-only linking cannot override the original account")
        return self


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


class ImportConfirmPreviewInput(PreviewExpected):
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_rows: list[RowIdentity] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def distinct_rows(self):
        if len({(row.file_id, row.source_row_number) for row in self.selected_rows}) != len(self.selected_rows):
            raise ValueError("duplicate selected row")
        return self


class ImportConfirmInput(ImportConfirmPreviewInput):
    batch_preview_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ImportOperationPreviewInput(ImportConfirmPreviewInput):
    """One informed operation, not an enlarged financial transaction."""
    selected_rows: list[RowIdentity] = Field(min_length=1, max_length=20000)


class ImportOperationApproveInput(ImportOperationPreviewInput):
    operation_preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ImportOperationConfirmInput(ImportConfirmInput):
    operation_preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    batch_index: StrictInt = Field(ge=0, lt=20000)
    batch_preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ImportOperationStopInput(ImportInput):
    operation_preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ImportBindingPreviewInput(ImportReviseInput):
    """Readonly complete proposed source binding; not an enlarged PUT/write."""
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    account_ref_id: NonnegativeId | None
    choices: list[RowChoice] = Field(min_length=1, max_length=20000)


class ImportPairingPreviewInput(ImportReviseInput):
    """Complete readonly suggestions, never mass consent or a larger write."""
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: Literal["SAME_SOURCE", "CROSS_SOURCE"]
    choices: list[RowChoice] = Field(min_length=1, max_length=20000)


class ImportRepeatPreviewInput(ImportReviseInput):
    """Readonly complete repeat-export suggestions; never saves intent."""
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    choices: list[RowChoice] = Field(min_length=1, max_length=20000)


class PreviewRowListRequest(ListRequest):
    pass


class ImportMatchListRequest(ListRequest):
    """Exact source-row scope; no additional text/filter/sort capability."""
