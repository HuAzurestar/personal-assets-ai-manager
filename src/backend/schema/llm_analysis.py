"""Strict DTOs for one privacy-filtered LLM tag-analysis request."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LlmCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tag_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    status: Literal["ACTIVE"] = "ACTIVE"

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("candidate name cannot be blank")
        return normalized


class LlmAmountDisclosure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["BAND", "EXACT", "NONE"]
    currency_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,11}$")
    band_code: str | None = Field(default=None, min_length=1, max_length=64)
    band_label: str | None = Field(default=None, min_length=1, max_length=120)
    amount_units: int | None = None

    @model_validator(mode="after")
    def validate_mode_fields(self) -> LlmAmountDisclosure:
        if self.mode == "BAND":
            if self.band_code is None or self.band_label is None:
                raise ValueError("BAND requires band_code and band_label")
            if self.amount_units is not None:
                raise ValueError("BAND cannot disclose exact amount units")
        elif self.mode == "EXACT":
            if self.amount_units is None:
                raise ValueError("EXACT requires amount_units")
            if self.band_code is not None or self.band_label is not None:
                raise ValueError("EXACT cannot include amount band fields")
        elif any(
            value is not None
            for value in (self.band_code, self.band_label, self.amount_units)
        ):
            raise ValueError("NONE cannot include amount information")
        return self


class _LlmAnalysisInput(BaseModel):
    """Common provider payload after the source-specific safety boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)

    item: str = Field(min_length=1, max_length=120)
    direction: Literal["IN", "OUT"]
    merchant: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=500)
    rule_prompt: str = Field(min_length=1, max_length=4000)
    amount: LlmAmountDisclosure
    candidates: list[LlmCandidate] = Field(min_length=1, max_length=100)

    @field_validator("item", "rule_prompt")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text cannot be blank")
        return normalized

    @field_validator("merchant", "summary")
    @classmethod
    def normalize_optional_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def reject_duplicate_candidates(self) -> _LlmAnalysisInput:
        candidate_ids = [candidate.tag_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate tag ids must be unique")
        return self


class SyntheticLlmAnalysisInput(_LlmAnalysisInput):
    """Auditable test input; raw Ledger DTOs cannot enter this contract."""

    source: Literal["SYNTHETIC_FIXTURE"]


class ProtectedLlmAnalysisInput(_LlmAnalysisInput):
    """Production input containing only privacy-filtered business fields."""

    source: Literal["PROTECTED_LEDGER"]


LlmAnalysisInput = SyntheticLlmAnalysisInput | ProtectedLlmAnalysisInput


class LlmResolvedSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tag_id: int = Field(ge=1)
    tag_name: str
    reason: str


class LlmAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["SUGGESTED", "NO_SUGGESTION"]
    item: str
    suggestions: list[LlmResolvedSuggestion]
