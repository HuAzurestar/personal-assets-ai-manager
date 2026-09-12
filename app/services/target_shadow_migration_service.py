from __future__ import annotations

from sqlalchemy.orm import Session

from app.schemas.target_shadow import TargetReviewMigrationRead, TargetShadowMigrationRead
from app.services.account_migration_service import AccountReviewShadowMigrationService
from app.services.candidate_migration_service import CandidateReviewShadowMigrationService
from app.services.import_issue_migration_service import ImportIssueReviewShadowMigrationService
from app.services.ledger_projection_migration_service import (
    LedgerProjectionShadowMigrationService,
)
from app.services.refund_migration_service import RefundReviewShadowMigrationService
from app.services.review_migration_service import ReviewMatterShadowMigrationService
from app.services.tag_dictionary_migration_service import (
    TagDictionaryShadowMigrationService,
)
from app.services.tag_migration_service import TagReviewShadowMigrationService
from app.services.target_ledger_status_service import TargetLedgerStatusService
from app.services.target_migration_service import FactShadowMigrationService


class TargetShadowMigrationService:
    """Run the complete idempotent shadow pipeline through one use case."""

    def __init__(self, db: Session):
        self.db = db

    def backfill_and_compare(self) -> TargetShadowMigrationRead:
        facts = FactShadowMigrationService(self.db).backfill_and_compare()
        reviews = TargetReviewMigrationRead(
            manual_matters=ReviewMatterShadowMigrationService(
                self.db
            ).backfill_and_compare(),
            candidates=CandidateReviewShadowMigrationService(
                self.db
            ).backfill_and_compare(),
            refunds=RefundReviewShadowMigrationService(
                self.db
            ).backfill_and_compare(),
            accounts=AccountReviewShadowMigrationService(
                self.db
            ).backfill_and_compare(),
            tags=TagReviewShadowMigrationService(self.db).backfill_and_compare(),
            import_issues=ImportIssueReviewShadowMigrationService(
                self.db
            ).backfill_and_compare(),
        )
        tag_dictionary = TagDictionaryShadowMigrationService(
            self.db
        ).backfill_and_compare()
        projection = LedgerProjectionShadowMigrationService(
            self.db
        ).backfill_and_compare()
        status = TargetLedgerStatusService(self.db).status()
        reports = [
            facts,
            reviews.manual_matters,
            reviews.candidates,
            reviews.refunds,
            reviews.accounts,
            reviews.tags,
            reviews.import_issues,
            tag_dictionary,
            projection,
        ]
        return TargetShadowMigrationRead(
            matched=all(report.matched for report in reports) and status.ready,
            facts=facts,
            reviews=reviews,
            tag_dictionary=tag_dictionary,
            projection=projection,
            status=status,
        )
