from __future__ import annotations

from pydantic import BaseModel

from app.schemas.migration import (
    FactShadowReport,
    LedgerProjectionShadowReport,
    ReviewShadowReport,
    TagDictionaryShadowReport,
)
from app.schemas.target_ledger import TargetLedgerShadowStatusRead


class TargetReviewMigrationRead(BaseModel):
    manual_matters: ReviewShadowReport
    candidates: ReviewShadowReport
    refunds: ReviewShadowReport
    accounts: ReviewShadowReport
    tags: ReviewShadowReport
    import_issues: ReviewShadowReport


class TargetShadowMigrationRead(BaseModel):
    matched: bool
    facts: FactShadowReport
    reviews: TargetReviewMigrationRead
    tag_dictionary: TagDictionaryShadowReport
    projection: LedgerProjectionShadowReport
    status: TargetLedgerShadowStatusRead
