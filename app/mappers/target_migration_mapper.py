from __future__ import annotations

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.database import (
    AccountRevision,
    Bill,
    CandidateActionLog,
    ImportArtifact,
    ImportBatch,
    ImportIssueAction,
    ImportRowIssue,
    LedgerOrigin,
    RefundAllocation,
    RefundAllocationAudit,
    RefundDesignation,
    RefundNatureAudit,
    ReviewMatter,
    ReviewMatterRevision,
    ReviewCandidate,
    TagAudit,
    TagView,
    ViewTag,
)
from app.models.target import (
    BillFact,
    BillRaw,
    ImportFile,
    LedgerEntry,
    LedgerEntrySource,
    LedgerEntryTag,
    ReviewCase,
    ReviewCaseBill,
    ReviewHistory,
    TargetTag,
    TargetTagView,
)
from app.schemas.migration import (
    BillFactShadowVO,
    BillRawShadowVO,
    ImportFileShadowVO,
    LegacyBillVO,
    LegacyAccountBillVO,
    LegacyAccountRevisionVO,
    LegacyCandidateActionVO,
    LegacyCandidateVO,
    LegacyImportArtifactVO,
    LegacyImportBatchVO,
    LegacyImportIssueActionVO,
    LegacyImportIssueVO,
    LegacyOriginVO,
    LegacyRefundAllocationAuditVO,
    LegacyRefundAllocationVO,
    LegacyRefundDesignationVO,
    LegacyRefundNatureAuditVO,
    LegacyTagAuditVO,
    LegacyTagBillVO,
    LegacyTagValueVO,
    LegacyTagViewVO,
    LedgerEntryShadowVO,
    LedgerEntrySourceShadowVO,
    LedgerEntryTagShadowVO,
    ProjectionFactVO,
    ProjectionReviewCaseVO,
    ProjectionReviewLineVO,
    LegacyMatterRevisionVO,
    LegacyMatterVO,
    ReviewCaseBillShadowVO,
    ReviewCaseShadowVO,
    ReviewHistoryShadowVO,
    TargetTagShadowVO,
    TargetTagViewShadowVO,
)


class TargetMigrationMapper:
    """Set-oriented SQL boundary for target-schema shadow migration."""

    def __init__(self, db: Session):
        self.db = db

    def legacy_bills(self) -> tuple[LegacyBillVO, ...]:
        rows = self.db.execute(select(
            Bill.id,
            Bill.occurred_at,
            Bill.merchant,
            Bill.note,
            Bill.amount,
            Bill.currency,
            Bill.account_name,
        ).order_by(Bill.id)).mappings().all()
        return tuple(LegacyBillVO(**row) for row in rows)

    def legacy_import_batches(self) -> tuple[LegacyImportBatchVO, ...]:
        rows = self.db.execute(select(
            ImportBatch.id,
            ImportBatch.source_type,
            ImportBatch.filename,
            ImportBatch.imported_at,
            ImportBatch.row_count,
            ImportBatch.imported_count,
            ImportBatch.batch_token,
        ).order_by(ImportBatch.id)).mappings().all()
        return tuple(LegacyImportBatchVO(**row) for row in rows)

    def legacy_import_artifacts(self) -> tuple[LegacyImportArtifactVO, ...]:
        rows = self.db.execute(select(
            ImportArtifact.id,
            ImportArtifact.import_batch_id,
            ImportArtifact.source_type,
            ImportArtifact.filename,
            ImportArtifact.file_format,
            ImportArtifact.sha256,
        ).order_by(ImportArtifact.import_batch_id, ImportArtifact.id)).mappings().all()
        return tuple(LegacyImportArtifactVO(**row) for row in rows)

    def legacy_origins(self) -> tuple[LegacyOriginVO, ...]:
        rows = self.db.execute(select(
            LedgerOrigin.id,
            LedgerOrigin.bill_id,
            LedgerOrigin.source_type,
            LedgerOrigin.source_reference,
            LedgerOrigin.raw_payload,
            LedgerOrigin.import_batch_id,
            LedgerOrigin.source_row_number,
        ).order_by(LedgerOrigin.id)).mappings().all()
        return tuple(LegacyOriginVO(**row) for row in rows)

    def legacy_import_issues(self) -> tuple[LegacyImportIssueVO, ...]:
        rows = self.db.execute(select(
            ImportRowIssue.id,
            ImportRowIssue.import_batch_id,
            ImportRowIssue.source_row_number,
            ImportRowIssue.raw_payload,
            ImportRowIssue.error,
            ImportRowIssue.resolution,
            ImportRowIssue.bill_id,
            ImportRowIssue.resolved_at,
            ImportBatch.imported_at.label("created_at"),
        ).outerjoin(
            ImportBatch,
            ImportBatch.id == ImportRowIssue.import_batch_id,
        ).order_by(ImportRowIssue.id)).mappings().all()
        return tuple(LegacyImportIssueVO(**row) for row in rows)

    def legacy_import_issue_actions(self) -> tuple[LegacyImportIssueActionVO, ...]:
        rows = self.db.execute(select(
            ImportIssueAction.id,
            ImportIssueAction.issue_id,
            ImportIssueAction.action,
            ImportIssueAction.payload,
            ImportIssueAction.actor,
            ImportIssueAction.created_at,
        ).order_by(
            ImportIssueAction.issue_id,
            ImportIssueAction.id,
        )).mappings().all()
        return tuple(LegacyImportIssueActionVO(**row) for row in rows)

    def legacy_matters(self) -> tuple[LegacyMatterVO, ...]:
        rows = self.db.execute(select(
            ReviewMatter.id,
            ReviewMatter.version,
            ReviewMatter.created_at,
        ).order_by(ReviewMatter.id)).mappings().all()
        return tuple(LegacyMatterVO(**row) for row in rows)

    def legacy_matter_revisions(self) -> tuple[LegacyMatterRevisionVO, ...]:
        rows = self.db.execute(select(
            ReviewMatterRevision.id,
            ReviewMatterRevision.matter_id,
            ReviewMatterRevision.version,
            ReviewMatterRevision.action,
            ReviewMatterRevision.snapshot,
            ReviewMatterRevision.reason,
            ReviewMatterRevision.actor,
            ReviewMatterRevision.idempotency_key,
            ReviewMatterRevision.request_payload,
            ReviewMatterRevision.created_at,
        ).order_by(
            ReviewMatterRevision.matter_id,
            ReviewMatterRevision.version,
        )).mappings().all()
        return tuple(LegacyMatterRevisionVO(**row) for row in rows)

    def legacy_candidates(self) -> tuple[LegacyCandidateVO, ...]:
        rows = self.db.execute(select(
            ReviewCandidate.id,
            ReviewCandidate.candidate_type,
            ReviewCandidate.bill_id,
            ReviewCandidate.related_bill_id,
            ReviewCandidate.member_bill_ids,
            ReviewCandidate.confidence,
            ReviewCandidate.reason,
            ReviewCandidate.status,
            ReviewCandidate.group_fingerprint,
            ReviewCandidate.superseded_by_id,
            ReviewCandidate.transfer_group_id,
            ReviewCandidate.transfer_kind,
            ReviewCandidate.retained_bill_id,
            ReviewCandidate.resolved_at,
            ReviewCandidate.created_at,
        ).order_by(ReviewCandidate.id)).mappings().all()
        return tuple(LegacyCandidateVO(**row) for row in rows)

    def legacy_candidate_actions(self) -> tuple[LegacyCandidateActionVO, ...]:
        rows = self.db.execute(select(
            CandidateActionLog.id,
            CandidateActionLog.candidate_id,
            CandidateActionLog.action,
            CandidateActionLog.before_state,
            CandidateActionLog.after_state,
            CandidateActionLog.actor,
            CandidateActionLog.reason,
            CandidateActionLog.reverses_action_id,
            CandidateActionLog.idempotency_key,
            CandidateActionLog.request_payload,
            CandidateActionLog.created_at,
            CandidateActionLog.undone,
            CandidateActionLog.undone_at,
        ).order_by(
            CandidateActionLog.candidate_id,
            CandidateActionLog.id,
        )).mappings().all()
        return tuple(LegacyCandidateActionVO(**row) for row in rows)

    def legacy_refund_allocations(self) -> tuple[LegacyRefundAllocationVO, ...]:
        rows = self.db.execute(select(
            RefundAllocation.id,
            RefundAllocation.refund_bill_id,
            RefundAllocation.expense_bill_id,
            RefundAllocation.amount,
            RefundAllocation.status,
            RefundAllocation.idempotency_key,
            RefundAllocation.request_payload,
            RefundAllocation.created_at,
            RefundAllocation.revoked_at,
        ).order_by(RefundAllocation.refund_bill_id, RefundAllocation.id)).mappings().all()
        return tuple(LegacyRefundAllocationVO(**row) for row in rows)

    def legacy_refund_allocation_audits(self) -> tuple[LegacyRefundAllocationAuditVO, ...]:
        rows = self.db.execute(select(
            RefundAllocationAudit.id,
            RefundAllocationAudit.allocation_id,
            RefundAllocationAudit.action,
            RefundAllocationAudit.actor,
            RefundAllocationAudit.reason,
            RefundAllocationAudit.before_state,
            RefundAllocationAudit.after_state,
            RefundAllocationAudit.reverses_audit_id,
            RefundAllocationAudit.idempotency_key,
            RefundAllocationAudit.request_payload,
            RefundAllocationAudit.created_at,
        ).order_by(
            RefundAllocationAudit.allocation_id,
            RefundAllocationAudit.id,
        )).mappings().all()
        return tuple(LegacyRefundAllocationAuditVO(**row) for row in rows)

    def legacy_refund_designations(self) -> tuple[LegacyRefundDesignationVO, ...]:
        rows = self.db.execute(select(
            RefundDesignation.bill_id,
            RefundDesignation.created_at,
        ).order_by(RefundDesignation.bill_id)).mappings().all()
        return tuple(LegacyRefundDesignationVO(**row) for row in rows)

    def legacy_refund_nature_audits(self) -> tuple[LegacyRefundNatureAuditVO, ...]:
        rows = self.db.execute(select(
            RefundNatureAudit.id,
            RefundNatureAudit.bill_id,
            RefundNatureAudit.action,
            RefundNatureAudit.reason,
            RefundNatureAudit.actor,
            RefundNatureAudit.before_nature,
            RefundNatureAudit.after_nature,
            RefundNatureAudit.idempotency_key,
            RefundNatureAudit.request_payload,
            RefundNatureAudit.created_at,
        ).order_by(
            RefundNatureAudit.bill_id,
            RefundNatureAudit.id,
        )).mappings().all()
        return tuple(LegacyRefundNatureAuditVO(**row) for row in rows)

    def legacy_account_revisions(self) -> tuple[LegacyAccountRevisionVO, ...]:
        rows = self.db.execute(select(
            AccountRevision.id,
            AccountRevision.bill_id,
            AccountRevision.before_account,
            AccountRevision.after_account,
            AccountRevision.action,
            AccountRevision.actor,
            AccountRevision.reason,
            AccountRevision.reverses_revision_id,
            AccountRevision.undone,
            AccountRevision.undone_at,
            AccountRevision.idempotency_key,
            AccountRevision.request_payload,
            AccountRevision.created_at,
        ).order_by(AccountRevision.bill_id, AccountRevision.id)).mappings().all()
        return tuple(LegacyAccountRevisionVO(**row) for row in rows)

    def legacy_account_bills(self, bill_ids: list[int]) -> tuple[LegacyAccountBillVO, ...]:
        if not bill_ids:
            return ()
        rows = self.db.execute(select(
            Bill.id,
            Bill.account_name,
        ).where(Bill.id.in_(bill_ids)).order_by(Bill.id)).mappings().all()
        return tuple(LegacyAccountBillVO(**row) for row in rows)

    def legacy_tag_audits(self) -> tuple[LegacyTagAuditVO, ...]:
        rows = self.db.execute(select(
            TagAudit.id,
            TagAudit.bill_id,
            TagAudit.category,
            TagAudit.tags,
            TagAudit.tag_state_json,
            TagAudit.strategy,
            TagAudit.confidence,
            TagAudit.provider,
            TagAudit.superseded,
            TagAudit.action,
            TagAudit.actor,
            TagAudit.reason,
            TagAudit.before_state_json,
            TagAudit.before_category,
            TagAudit.reverses_audit_id,
            TagAudit.undone,
            TagAudit.undone_at,
            TagAudit.idempotency_key,
            TagAudit.request_payload,
            TagAudit.created_at,
        ).order_by(TagAudit.bill_id, TagAudit.id)).mappings().all()
        return tuple(LegacyTagAuditVO(**row) for row in rows)

    def legacy_tag_bills(self, bill_ids: list[int]) -> tuple[LegacyTagBillVO, ...]:
        if not bill_ids:
            return ()
        rows = self.db.execute(select(
            Bill.id,
            Bill.category,
            Bill.tag_state_json,
        ).where(Bill.id.in_(bill_ids)).order_by(Bill.id)).mappings().all()
        return tuple(LegacyTagBillVO(**row) for row in rows)

    def legacy_tag_views(self) -> tuple[LegacyTagViewVO, ...]:
        rows = self.db.execute(select(
            TagView.id,
            TagView.name,
            TagView.system_name,
            TagView.archived,
            TagView.created_at,
        ).order_by(TagView.id)).mappings().all()
        return tuple(LegacyTagViewVO(**row) for row in rows)

    def legacy_tag_values(self) -> tuple[LegacyTagValueVO, ...]:
        rows = self.db.execute(select(
            ViewTag.id,
            ViewTag.view_id,
            ViewTag.name,
            ViewTag.system_name,
            ViewTag.is_unclassified,
            ViewTag.archived,
        ).order_by(ViewTag.view_id, ViewTag.id)).mappings().all()
        return tuple(LegacyTagValueVO(**row) for row in rows)

    def target_import_files(self) -> tuple[ImportFileShadowVO, ...]:
        rows = self.db.execute(select(
            ImportFile.id,
            ImportFile.batch_code,
            ImportFile.source_type,
            ImportFile.institution_code,
            ImportFile.filename,
            ImportFile.file_format,
            ImportFile.sha256,
            ImportFile.period_start,
            ImportFile.period_end,
            ImportFile.total_count,
            ImportFile.success_count,
            ImportFile.skip_count,
            ImportFile.issue_count,
            ImportFile.status,
        ).order_by(ImportFile.id)).mappings().all()
        return tuple(ImportFileShadowVO(**row) for row in rows)

    def target_bill_raws(self, raw_ids: list[int] | None = None) -> tuple[BillRawShadowVO, ...]:
        if raw_ids is not None and not raw_ids:
            return ()
        statement = select(
            BillRaw.id,
            BillRaw.bill_id,
            BillRaw.import_file_id,
            BillRaw.source_row_number,
            BillRaw.source_reference,
            BillRaw.raw_payload,
            BillRaw.raw_hash,
            BillRaw.parse_status,
            BillRaw.issue_code,
            BillRaw.issue_message,
        )
        if raw_ids is not None:
            statement = statement.where(BillRaw.id.in_(raw_ids))
        rows = self.db.execute(statement.order_by(BillRaw.id)).mappings().all()
        return tuple(BillRawShadowVO(**row) for row in rows)

    def target_bill_facts(self, bill_ids: list[int] | None = None) -> tuple[BillFactShadowVO, ...]:
        if bill_ids is not None and not bill_ids:
            return ()
        statement = select(
            BillFact.id,
            BillFact.fact_key,
            BillFact.occurred_time,
            BillFact.cash_direction,
            BillFact.amount_value,
            BillFact.amount_scale,
            BillFact.currency_code,
            BillFact.account_code,
            BillFact.counterparty,
            BillFact.summary,
        )
        if bill_ids is not None:
            statement = statement.where(BillFact.id.in_(bill_ids))
        rows = self.db.execute(statement.order_by(BillFact.id)).mappings().all()
        return tuple(BillFactShadowVO(**row) for row in rows)

    def target_review_cases(
        self,
        *,
        case_id_start: int,
        case_id_end: int,
    ) -> tuple[ReviewCaseShadowVO, ...]:
        rows = self.db.execute(select(
            ReviewCase.id,
            ReviewCase.created_time,
            ReviewCase.updated_time,
            ReviewCase.review_type,
            ReviewCase.status,
            ReviewCase.allocation_status,
            ReviewCase.version,
            ReviewCase.title,
            ReviewCase.result_json,
        ).where(
            ReviewCase.id >= case_id_start,
            ReviewCase.id < case_id_end,
        ).order_by(ReviewCase.id)).mappings().all()
        return tuple(ReviewCaseShadowVO(**row) for row in rows)

    def target_review_case_bills(
        self,
        *,
        case_id_start: int,
        case_id_end: int,
    ) -> tuple[ReviewCaseBillShadowVO, ...]:
        rows = self.db.execute(select(
            ReviewCaseBill.id,
            ReviewCaseBill.created_time,
            ReviewCaseBill.updated_time,
            ReviewCaseBill.case_id,
            ReviewCaseBill.bill_id,
            ReviewCaseBill.role,
            ReviewCaseBill.party,
            ReviewCaseBill.amount_value,
            ReviewCaseBill.amount_scale,
            ReviewCaseBill.currency_code,
        ).where(
            ReviewCaseBill.case_id >= case_id_start,
            ReviewCaseBill.case_id < case_id_end,
        ).order_by(ReviewCaseBill.id)).mappings().all()
        return tuple(ReviewCaseBillShadowVO(**row) for row in rows)

    def target_review_history(
        self,
        *,
        case_id_start: int,
        case_id_end: int,
    ) -> tuple[ReviewHistoryShadowVO, ...]:
        rows = self.db.execute(select(
            ReviewHistory.id,
            ReviewHistory.created_time,
            ReviewHistory.updated_time,
            ReviewHistory.case_id,
            ReviewHistory.version,
            ReviewHistory.operation,
            ReviewHistory.schema_version,
            ReviewHistory.request_json,
            ReviewHistory.before_json,
            ReviewHistory.after_json,
            ReviewHistory.snapshot_hash,
            ReviewHistory.reverses_history_id,
            ReviewHistory.actor,
            ReviewHistory.reason,
            ReviewHistory.idempotency_key,
        ).where(
            ReviewHistory.case_id >= case_id_start,
            ReviewHistory.case_id < case_id_end,
        ).order_by(ReviewHistory.id)).mappings().all()
        return tuple(ReviewHistoryShadowVO(**row) for row in rows)

    def target_tag_views(self) -> tuple[TargetTagViewShadowVO, ...]:
        rows = self.db.execute(select(
            TargetTagView.id,
            TargetTagView.created_time,
            TargetTagView.updated_time,
            TargetTagView.name,
            TargetTagView.system_name,
            TargetTagView.status,
        ).order_by(TargetTagView.id)).mappings().all()
        return tuple(TargetTagViewShadowVO(**row) for row in rows)

    def target_tags(self) -> tuple[TargetTagShadowVO, ...]:
        rows = self.db.execute(select(
            TargetTag.id,
            TargetTag.created_time,
            TargetTag.updated_time,
            TargetTag.view_id,
            TargetTag.name,
            TargetTag.system_name,
            TargetTag.status,
        ).order_by(TargetTag.view_id, TargetTag.id)).mappings().all()
        return tuple(TargetTagShadowVO(**row) for row in rows)

    def projection_facts(self) -> tuple[ProjectionFactVO, ...]:
        rows = self.db.execute(select(
            BillFact.id,
            BillFact.created_time,
            BillFact.updated_time,
            BillFact.fact_key,
            BillFact.occurred_time,
            BillFact.cash_direction,
            BillFact.amount_value,
            BillFact.amount_scale,
            BillFact.currency_code,
            BillFact.account_code,
            BillFact.counterparty,
            BillFact.summary,
        ).order_by(BillFact.id)).mappings().all()
        return tuple(ProjectionFactVO(**row) for row in rows)

    def projection_confirmed_cases(self) -> tuple[ProjectionReviewCaseVO, ...]:
        rows = self.db.execute(select(
            ReviewCase.id,
            ReviewCase.created_time,
            ReviewCase.updated_time,
            ReviewCase.review_type,
            ReviewCase.status,
            ReviewCase.allocation_status,
            ReviewCase.version,
            ReviewCase.title,
            ReviewCase.result_json,
        ).where(
            ReviewCase.status == "CONFIRMED",
        ).order_by(ReviewCase.id)).mappings().all()
        return tuple(ProjectionReviewCaseVO(**row) for row in rows)

    def projection_review_lines(self, case_ids: list[int]) -> tuple[ProjectionReviewLineVO, ...]:
        if not case_ids:
            return ()
        rows = self.db.execute(select(
            ReviewCaseBill.id,
            ReviewCaseBill.case_id,
            ReviewCaseBill.bill_id,
            ReviewCaseBill.role,
            ReviewCaseBill.party,
            ReviewCaseBill.amount_value,
            ReviewCaseBill.amount_scale,
            ReviewCaseBill.currency_code,
        ).where(
            ReviewCaseBill.case_id.in_(case_ids),
        ).order_by(
            ReviewCaseBill.case_id,
            ReviewCaseBill.id,
        )).mappings().all()
        return tuple(ProjectionReviewLineVO(**row) for row in rows)

    def target_ledger_entries(self) -> tuple[LedgerEntryShadowVO, ...]:
        rows = self.db.execute(select(
            LedgerEntry.id,
            LedgerEntry.created_time,
            LedgerEntry.updated_time,
            LedgerEntry.ledger_type,
            LedgerEntry.allocation_status,
            LedgerEntry.title,
            LedgerEntry.start_time,
            LedgerEntry.end_time,
            LedgerEntry.in_amount_value,
            LedgerEntry.in_amount_scale,
            LedgerEntry.in_currency_code,
            LedgerEntry.out_amount_value,
            LedgerEntry.out_amount_scale,
            LedgerEntry.out_currency_code,
            LedgerEntry.in_account_code,
            LedgerEntry.out_account_code,
            LedgerEntry.input_hash,
            LedgerEntry.projection_version,
        ).order_by(LedgerEntry.id)).mappings().all()
        return tuple(LedgerEntryShadowVO(**row) for row in rows)

    def target_ledger_entry_sources(self) -> tuple[LedgerEntrySourceShadowVO, ...]:
        rows = self.db.execute(select(
            LedgerEntrySource.id,
            LedgerEntrySource.created_time,
            LedgerEntrySource.updated_time,
            LedgerEntrySource.ledger_id,
            LedgerEntrySource.source_kind,
            LedgerEntrySource.source_id,
        ).order_by(LedgerEntrySource.id)).mappings().all()
        return tuple(LedgerEntrySourceShadowVO(**row) for row in rows)

    def target_ledger_entry_tags(self) -> tuple[LedgerEntryTagShadowVO, ...]:
        rows = self.db.execute(select(
            LedgerEntryTag.id,
            LedgerEntryTag.created_time,
            LedgerEntryTag.updated_time,
            LedgerEntryTag.ledger_id,
            LedgerEntryTag.tag_id,
        ).order_by(LedgerEntryTag.id)).mappings().all()
        return tuple(LedgerEntryTagShadowVO(**row) for row in rows)

    def insert_import_files(self, values: list[dict[str, object]]) -> None:
        if values:
            self.db.execute(insert(ImportFile), values)

    def insert_bill_raws(self, values: list[dict[str, object]]) -> None:
        if values:
            self.db.execute(insert(BillRaw), values)

    def insert_bill_facts(self, values: list[dict[str, object]]) -> None:
        if values:
            self.db.execute(insert(BillFact), values)

    def insert_review_cases(self, values: list[dict[str, object]]) -> None:
        if values:
            self.db.execute(insert(ReviewCase), values)

    def insert_review_case_bills(self, values: list[dict[str, object]]) -> None:
        if values:
            self.db.execute(insert(ReviewCaseBill), values)

    def insert_review_history(self, values: list[dict[str, object]]) -> None:
        if values:
            self.db.execute(insert(ReviewHistory), values)

    def insert_target_tag_views(self, values: list[dict[str, object]]) -> None:
        if values:
            self.db.execute(insert(TargetTagView), values)

    def insert_target_tags(self, values: list[dict[str, object]]) -> None:
        if values:
            self.db.execute(insert(TargetTag), values)

    def insert_ledger_entries(self, values: list[dict[str, object]]) -> None:
        if values:
            self.db.execute(insert(LedgerEntry), values)

    def insert_ledger_entry_sources(self, values: list[dict[str, object]]) -> None:
        if values:
            self.db.execute(insert(LedgerEntrySource), values)

    def insert_ledger_entry_tags(self, values: list[dict[str, object]]) -> None:
        if values:
            self.db.execute(insert(LedgerEntryTag), values)
